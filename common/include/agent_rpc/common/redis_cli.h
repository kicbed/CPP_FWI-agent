#pragma once

#include <cerrno>
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include <string>
#include <vector>

extern char** environ;

namespace agent_rpc::common {

// Run the fixed local Redis CLI with an argv array. No argument is interpreted
// by a shell, so session IDs cannot become commands or redirections.
inline std::string run_redis_cli(const std::vector<std::string>& arguments) {
    if (arguments.empty()) return {};

    int output_pipe[2];
    if (::pipe(output_pipe) != 0) return {};

    posix_spawn_file_actions_t actions;
    if (posix_spawn_file_actions_init(&actions) != 0) {
        ::close(output_pipe[0]);
        ::close(output_pipe[1]);
        return {};
    }

    int rc = posix_spawn_file_actions_addopen(
        &actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    if (rc == 0) {
        rc = posix_spawn_file_actions_adddup2(
            &actions, output_pipe[1], STDOUT_FILENO);
    }
    if (rc == 0) {
        rc = posix_spawn_file_actions_addopen(
            &actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0);
    }
    if (rc == 0) rc = posix_spawn_file_actions_addclose(&actions, output_pipe[0]);
    if (rc == 0) rc = posix_spawn_file_actions_addclose(&actions, output_pipe[1]);
    if (rc != 0) {
        posix_spawn_file_actions_destroy(&actions);
        ::close(output_pipe[0]);
        ::close(output_pipe[1]);
        return {};
    }

    std::vector<std::string> storage = {"/usr/bin/redis-cli", "--raw"};
    storage.insert(storage.end(), arguments.begin(), arguments.end());
    std::vector<char*> argv;
    argv.reserve(storage.size() + 1);
    for (auto& value : storage) argv.push_back(value.data());
    argv.push_back(nullptr);

    pid_t pid = -1;
    rc = posix_spawn(
        &pid, storage.front().c_str(), &actions, nullptr, argv.data(), environ);
    posix_spawn_file_actions_destroy(&actions);
    ::close(output_pipe[1]);
    if (rc != 0) {
        ::close(output_pipe[0]);
        return {};
    }

    std::string output;
    char buffer[8192];
    while (true) {
        const ssize_t bytes = ::read(output_pipe[0], buffer, sizeof(buffer));
        if (bytes > 0) {
            output.append(buffer, static_cast<std::size_t>(bytes));
            continue;
        }
        if (bytes < 0 && errno == EINTR) continue;
        break;
    }
    ::close(output_pipe[0]);

    int status = 0;
    pid_t waited = -1;
    do {
        waited = ::waitpid(pid, &status, 0);
    } while (waited < 0 && errno == EINTR);
    if (waited != pid || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        return {};
    }
    return output;
}

}  // namespace agent_rpc::common
