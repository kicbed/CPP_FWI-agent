#include <agent_rpc/orchestrator/vector_store.h>

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <sys/stat.h>
#include <unistd.h>

namespace {

namespace fs = std::filesystem;
using agent_rpc::orchestrator::VectorStore;

class TemporaryDirectory {
public:
    TemporaryDirectory() {
        std::string pattern =
            (fs::temp_directory_path() / "vector-store-security-XXXXXX").string();
        buffer_.assign(pattern.begin(), pattern.end());
        buffer_.push_back('\0');
        char* created = ::mkdtemp(buffer_.data());
        if (created == nullptr) throw std::runtime_error("mkdtemp failed");
        path_ = created;
        ::chmod(path_.c_str(), 0700);
    }
    ~TemporaryDirectory() {
        std::error_code error;
        fs::remove_all(path_, error);
    }
    const fs::path& path() const { return path_; }
private:
    std::vector<char> buffer_;
    fs::path path_;
};

std::string read_text(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(input),
                       std::istreambuf_iterator<char>());
}

TEST(VectorStoreSecurityTest, RejectsRelativeAndRootDirectories) {
    EXPECT_THROW(VectorStore("resources/embeddings"), std::invalid_argument);
    EXPECT_THROW(VectorStore("/"), std::invalid_argument);
    EXPECT_THROW(VectorStore("/tmp/safe/../escape"), std::invalid_argument);
}

TEST(VectorStoreSecurityTest, SavesAndLoadsPrivateAtomicCache) {
    TemporaryDirectory temporary;
    const fs::path cache = temporary.path() / "cache" / "embeddings";

    VectorStore writer(cache.string());
    writer.put("agent_cards", "agent-1", {1.0F, 2.0F, 3.0F});
    ASSERT_TRUE(writer.save("agent_cards"));

    struct stat info {};
    ASSERT_EQ(::lstat((cache / "agent_cards.json").c_str(), &info), 0);
    EXPECT_TRUE(S_ISREG(info.st_mode));
    EXPECT_EQ(info.st_mode & 0777U, 0600U);

    VectorStore reader(cache.string());
    ASSERT_TRUE(reader.load("agent_cards"));
    EXPECT_EQ(reader.get("agent_cards", "agent-1"),
              (std::vector<float>{1.0F, 2.0F, 3.0F}));

    const nlohmann::json metadata = {{"schema_version", 1}, {"dimension", 3}};
    ASSERT_TRUE(reader.saveJsonDocument("agent_cards.meta.json", metadata, 4096));
    nlohmann::json loaded;
    ASSERT_TRUE(reader.loadJsonDocument(
        "agent_cards.meta.json", &loaded, 4096));
    EXPECT_EQ(loaded, metadata);
}

TEST(VectorStoreSecurityTest, AtomicRenameDoesNotFollowDestinationSymlink) {
    TemporaryDirectory temporary;
    const fs::path cache = temporary.path() / "cache";
    fs::create_directory(cache);
    ::chmod(cache.c_str(), 0700);
    const fs::path sentinel = temporary.path() / "sentinel.txt";
    {
        std::ofstream output(sentinel);
        output << "do-not-change";
    }
    ASSERT_EQ(::symlink(sentinel.c_str(), (cache / "agent_cards.json").c_str()), 0);

    VectorStore store(cache.string());
    store.put("agent_cards", "agent-1", {4.0F});
    ASSERT_TRUE(store.save("agent_cards"));
    EXPECT_EQ(read_text(sentinel), "do-not-change");

    struct stat info {};
    ASSERT_EQ(::lstat((cache / "agent_cards.json").c_str(), &info), 0);
    EXPECT_TRUE(S_ISREG(info.st_mode));
    EXPECT_FALSE(S_ISLNK(info.st_mode));
}

TEST(VectorStoreSecurityTest, RejectsSymlinkedOrPublicCacheDirectory) {
    TemporaryDirectory temporary;
    const fs::path outside = temporary.path() / "outside";
    fs::create_directory(outside);
    ::chmod(outside.c_str(), 0700);
    const fs::path link = temporary.path() / "linked-cache";
    ASSERT_EQ(::symlink(outside.c_str(), link.c_str()), 0);

    VectorStore linked((link / "embeddings").string());
    linked.put("agent_cards", "agent-1", {1.0F});
    EXPECT_FALSE(linked.save("agent_cards"));
    EXPECT_FALSE(fs::exists(outside / "embeddings"));

    const fs::path public_cache = temporary.path() / "public-cache";
    fs::create_directory(public_cache);
    ::chmod(public_cache.c_str(), 0755);
    VectorStore public_store(public_cache.string());
    public_store.put("agent_cards", "agent-1", {1.0F});
    EXPECT_FALSE(public_store.save("agent_cards"));
    struct stat info {};
    ASSERT_EQ(::stat(public_cache.c_str(), &info), 0);
    EXPECT_EQ(info.st_mode & 0777U, 0755U);
}

}  // namespace
