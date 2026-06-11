#include "code_agent_tools.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <string>
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
    }

    void write_file(const std::string& relative_path, const std::string& content) {
        const auto path = root_ / relative_path;
        fs::create_directories(path.parent_path());
        std::ofstream out(path);
        out << content;
    }

    fs::path root_;
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
