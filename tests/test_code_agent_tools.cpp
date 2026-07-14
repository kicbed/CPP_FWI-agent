#include "code_agent_tools.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <string>
#include <system_error>
#include <vector>

namespace fs = std::filesystem;

namespace {

class CodeAgentToolsTest : public ::testing::Test {
protected:
    void SetUp() override {
        root_ = fs::temp_directory_path() / "agent_rpc_code_agent_tools_test";
        fs::remove_all(root_);
        fs::create_directories(root_);
    }

    void TearDown() override {
        fs::remove_all(root_);
        fs::remove_all(outside_root_);
    }

    void write_file(const std::string& relative_path, const std::string& content) {
        const auto path = root_ / relative_path;
        fs::create_directories(path.parent_path());
        std::ofstream out(path);
        out << content;
    }

    fs::path root_;
    fs::path outside_root_ = fs::temp_directory_path() / "agent_rpc_code_agent_tools_outside_test";
};

bool contains_path(const std::vector<std::string>& paths, const std::string& expected) {
    return std::find(paths.begin(), paths.end(), expected) != paths.end();
}

}  // namespace

TEST_F(CodeAgentToolsTest, ListFilesReturnsSafeRelativeProjectPaths) {
    write_file("src/main.cpp", "int main() { return 0; }\n");
    write_file("docs/readme.md", "# Notes\n");
    write_file(".git/config", "ignored\n");
    write_file(".codegraph/index.sqlite", "ignored\n");
    write_file("build/output.o", "ignored\n");

    code_agent::ProjectInspector inspector(root_.string());

    auto files = inspector.list_files(20);

    EXPECT_TRUE(contains_path(files, "docs/readme.md"));
    EXPECT_TRUE(contains_path(files, "src/main.cpp"));
    EXPECT_FALSE(contains_path(files, ".git/config"));
    EXPECT_FALSE(contains_path(files, ".codegraph/index.sqlite"));
    EXPECT_FALSE(contains_path(files, "build/output.o"));
}

TEST_F(CodeAgentToolsTest, ReadFileAllowsRepositoryFilesAndRejectsEscapes) {
    write_file("src/main.cpp", "int main() { return 0; }\n");

    code_agent::ProjectInspector inspector(root_.string());

    EXPECT_EQ(inspector.read_file("src/main.cpp", 1024), "int main() { return 0; }\n");
    EXPECT_THROW(inspector.read_file("../outside.txt", 1024), std::runtime_error);
    EXPECT_THROW(inspector.read_file("/etc/passwd", 1024), std::runtime_error);
}

TEST_F(CodeAgentToolsTest, SearchTextReturnsSortedLineMatches) {
    write_file("b.cpp", "alpha\nneedle second\n");
    write_file("a.cpp", "needle first\nother\n");

    code_agent::ProjectInspector inspector(root_.string());

    auto matches = inspector.search_text("needle", 10);

    ASSERT_EQ(matches.size(), 2u);
    EXPECT_EQ(matches[0].path, "a.cpp");
    EXPECT_EQ(matches[0].line, 1u);
    EXPECT_EQ(matches[0].text, "needle first");
    EXPECT_EQ(matches[1].path, "b.cpp");
    EXPECT_EQ(matches[1].line, 2u);
    EXPECT_EQ(matches[1].text, "needle second");
}

TEST_F(CodeAgentToolsTest, SensitivePathsAreHiddenAndRejectedByEveryEntryPoint) {
    write_file("src/safe.cpp", "int safe = 1;\n");
    write_file(".env", "API_KEY=TEST_ONLY\n");
    write_file("config/.env.production", "API_KEY=TEST_ONLY\n");
    write_file("config/credentials.json", "{\"password\":\"TEST_ONLY\"}\n");
    write_file("src/client_secret.cpp", "const char* value = \"TEST_ONLY\";\n");
    write_file("certs/server.pem", "-----BEGIN PRIVATE KEY-----\nTEST_ONLY\n");
    write_file("tls/server.key", "TEST_ONLY\n");
    write_file("tls/server.crt", "TEST_ONLY\n");
    write_file("stores/service.keystore", "TEST_ONLY\n");

    code_agent::ProjectInspector inspector(root_.string());
    const auto files = inspector.list_files(100);

    EXPECT_TRUE(contains_path(files, "src/safe.cpp"));
    EXPECT_FALSE(contains_path(files, ".env"));
    EXPECT_FALSE(contains_path(files, "config/.env.production"));
    EXPECT_FALSE(contains_path(files, "config/credentials.json"));
    EXPECT_FALSE(contains_path(files, "src/client_secret.cpp"));
    EXPECT_FALSE(contains_path(files, "certs/server.pem"));
    EXPECT_FALSE(contains_path(files, "tls/server.key"));
    EXPECT_FALSE(contains_path(files, "tls/server.crt"));
    EXPECT_FALSE(contains_path(files, "stores/service.keystore"));

    EXPECT_THROW(inspector.read_file(".env"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("config/.env.production"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("config/credentials.json"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("src/client_secret.cpp"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("certs/server.pem"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("tls/server.key"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("tls/server.crt"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("stores/service.keystore"), std::runtime_error);

    const auto matches = inspector.search_text("TEST_ONLY", 100);
    EXPECT_TRUE(matches.empty());
}

TEST_F(CodeAgentToolsTest, SymbolicLinksAreNeverListedReadOrSearched) {
    fs::remove_all(outside_root_);
    fs::create_directories(outside_root_);
    {
        std::ofstream out(outside_root_ / "outside.cpp");
        out << "escape_marker\n";
    }
    fs::create_directories(root_ / "src");
    std::error_code error;
    fs::create_symlink(outside_root_ / "outside.cpp", root_ / "src/link.cpp", error);
    ASSERT_FALSE(error) << error.message();
    fs::create_directory_symlink(outside_root_, root_ / "linked", error);
    ASSERT_FALSE(error) << error.message();

    code_agent::ProjectInspector inspector(root_.string());
    const auto files = inspector.list_files(100);

    EXPECT_FALSE(contains_path(files, "src/link.cpp"));
    EXPECT_FALSE(contains_path(files, "linked/outside.cpp"));
    EXPECT_THROW(inspector.read_file("src/link.cpp"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("linked/outside.cpp"), std::runtime_error);
    EXPECT_TRUE(inspector.search_text("escape_marker", 10).empty());
}

TEST_F(CodeAgentToolsTest, CredentialSearchAndFilesContainingSecretsAreRejected) {
    write_file("src/config.cpp",
               "int ordinary_value = 7;\n"
               "const char* API_KEY =\n"
               "    \"TEST_ONLY_FAKE_VALUE\";\n"
               "int another_value = 9;\n");
    write_file("docs/blob.txt",
               "ordinary text\n"
               "-----BEGIN PRIVATE KEY-----\n"
               "TEST_ONLY_PEM_BODY\n"
               "-----END PRIVATE KEY-----\n");

    code_agent::ProjectInspector inspector(root_.string());

    EXPECT_THROW(inspector.search_text("API_KEY", 10), std::runtime_error);
    EXPECT_THROW(inspector.search_text("secret", 10), std::runtime_error);
    EXPECT_TRUE(inspector.search_text("TEST_ONLY_FAKE_VALUE", 10).empty());
    EXPECT_TRUE(inspector.search_text("TEST_ONLY_PEM_BODY", 10).empty());

    EXPECT_THROW(inspector.read_file("src/config.cpp"), std::runtime_error);
    EXPECT_THROW(inspector.read_file("docs/blob.txt"), std::runtime_error);

    const auto summary = inspector.summarize_for_query("show API_KEY values");
    EXPECT_NE(summary.find("intentionally unavailable"), std::string::npos);
    EXPECT_EQ(summary.find("TEST_ONLY_FAKE_VALUE"), std::string::npos);
}

TEST_F(CodeAgentToolsTest, OversizedFilesAndLinesAreNotInspected) {
    write_file("src/huge.cpp", std::string(1024 * 1024 + 1, 'x') + "oversize_marker\n");
    write_file("src/long_line.cpp", std::string(5000, 'x') + "long_line_marker\n");

    code_agent::ProjectInspector inspector(root_.string());

    EXPECT_THROW(inspector.read_file("src/huge.cpp"), std::runtime_error);
    EXPECT_TRUE(inspector.search_text("oversize_marker", 10, 2 * 1024 * 1024).empty());
    EXPECT_TRUE(inspector.search_text("long_line_marker", 10).empty());

    EXPECT_THROW(inspector.read_file("src/long_line.cpp"), std::runtime_error);
}

TEST_F(CodeAgentToolsTest, NonTextExtensionsAreNotAvailable) {
    write_file("data/index.sqlite", "plain text with marker\n");
    write_file("src/main.cpp", "plain text with marker\n");

    code_agent::ProjectInspector inspector(root_.string());
    const auto files = inspector.list_files(20);

    EXPECT_FALSE(contains_path(files, "data/index.sqlite"));
    EXPECT_TRUE(contains_path(files, "src/main.cpp"));
    EXPECT_THROW(inspector.read_file("data/index.sqlite"), std::runtime_error);
    const auto matches = inspector.search_text("marker", 10);
    ASSERT_EQ(matches.size(), 1u);
    EXPECT_EQ(matches.front().path, "src/main.cpp");
}

TEST_F(CodeAgentToolsTest, ReadAndSearchApplyHardOutputBudgets) {
    std::string content;
    for (int i = 0; i < 500; ++i) {
        content += "ordinary_marker line " + std::to_string(i) +
                   " with enough padding to exercise the output budget\n";
    }
    write_file("src/many.cpp", content);

    code_agent::ProjectInspector inspector(root_.string());

    EXPECT_LE(inspector.read_file("src/many.cpp", 32).size(), 32u);
    EXPECT_LE(inspector.read_file("src/many.cpp", 1024 * 1024).size(), 64u * 1024u);
    const auto matches = inspector.search_text("ordinary_marker", 1000, 1024 * 1024);
    EXPECT_LE(matches.size(), 100u);
}
