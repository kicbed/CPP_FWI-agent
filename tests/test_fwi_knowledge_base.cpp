#include <agent_rpc/orchestrator/knowledge_base.h>

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
#include <unistd.h>

namespace {

using agent_rpc::orchestrator::KnowledgeBase;

class TemporaryCorpus {
public:
    TemporaryCorpus() {
        root = std::filesystem::temp_directory_path() /
            ("cpp-fwi-kb-test-" + std::to_string(::getpid()));
        outside = std::filesystem::temp_directory_path() /
            ("cpp-fwi-kb-outside-" + std::to_string(::getpid()));
        std::error_code ec;
        std::filesystem::remove_all(root, ec);
        std::filesystem::remove_all(outside, ec);
        std::filesystem::create_directories(root / "fwi_knowledge");
        std::filesystem::create_directories(root / "fwi_models");
        std::filesystem::create_directories(root / "fwi_datasets");
        std::filesystem::create_directories(outside);
    }

    ~TemporaryCorpus() {
        std::error_code ec;
        std::filesystem::remove_all(root, ec);
        std::filesystem::remove_all(outside, ec);
    }

    void write(const std::filesystem::path& relative,
               const std::string& content) const {
        const auto path = root / relative;
        std::filesystem::create_directories(path.parent_path());
        std::ofstream output(path, std::ios::binary);
        ASSERT_TRUE(output.is_open());
        output << content;
        ASSERT_TRUE(output.good());
    }

    std::filesystem::path root;
    std::filesystem::path outside;
};

void seed_theory_documents(const TemporaryCorpus& corpus) {
    corpus.write(
        "fwi_knowledge/fwi_basics.md",
        "# 全波形反演 (Full Waveform Inversion, FWI) 基础理论\n"
        "FWI 通过最小化观测数据与模拟数据的波形残差来更新速度模型。\n");
    corpus.write(
        "fwi_knowledge/cycle_skipping.md",
        "# Cycle Skipping (周波跳跃) 问题\n"
        "当走时差超过半个周期时，梯度可能指向错误方向。\n");
    corpus.write(
        "fwi_knowledge/adjoint_state.md",
        "# 伴随状态法 (Adjoint-State Method)\n"
        "一次正演和一次伴随传播可高效计算 FWI 梯度。\n");
    corpus.write(
        "fwi_knowledge/multiscale_fwi.md",
        "# 多尺度反演 (Multiscale FWI)\n"
        "从低频到高频进行频率递进，以降低周波跳跃风险。\n");
    corpus.write(
        "fwi_knowledge/awi.md",
        "# 自适应波形反演 (Adaptive Waveform Inversion, AWI)\n"
        "AWI 用自适应匹配降低 cycle skipping 风险。\n");
}

TEST(FWIKnowledgeBaseTest, RetrievesBilingualFWITopicsWithoutEmbedding) {
    TemporaryCorpus corpus;
    seed_theory_documents(corpus);
    KnowledgeBase knowledge;
    ASSERT_TRUE(knowledge.load(corpus.root.string()));

    const auto definition = knowledge.search("什么是 FWI？", 3);
    ASSERT_FALSE(definition.empty());
    EXPECT_EQ(definition.front().path, "fwi_knowledge/fwi_basics.md");

    const auto cycle = knowledge.search("为什么会发生周波跳跃？", 3);
    ASSERT_FALSE(cycle.empty());
    EXPECT_EQ(cycle.front().path, "fwi_knowledge/cycle_skipping.md");

    const auto adjoint = knowledge.search("伴随状态法怎样计算梯度？", 3);
    ASSERT_FALSE(adjoint.empty());
    EXPECT_EQ(adjoint.front().path, "fwi_knowledge/adjoint_state.md");

    const auto multiscale = knowledge.search("低频到高频的频率递进有什么作用？", 3);
    ASSERT_FALSE(multiscale.empty());
    EXPECT_EQ(multiscale.front().path, "fwi_knowledge/multiscale_fwi.md");

    // The Orchestrator uses this strong-match floor to catch specialist terms
    // even when the user omits the acronym FWI.
    EXPECT_GE(cycle.front().relevance_score, 7.0F);
    EXPECT_GE(adjoint.front().relevance_score, 7.0F);
    const auto ambiguous = knowledge.search("梯度怎么计算？", 1);
    ASSERT_FALSE(ambiguous.empty());
    EXPECT_LT(ambiguous.front().relevance_score, 7.0F);
}

TEST(FWIKnowledgeBaseTest, ReturnsNoDocumentsForUnrelatedQuestion) {
    TemporaryCorpus corpus;
    seed_theory_documents(corpus);
    KnowledgeBase knowledge;
    ASSERT_TRUE(knowledge.load(corpus.root.string()));

    EXPECT_TRUE(knowledge.search("今天天气怎么样？", 5).empty());
    EXPECT_TRUE(knowledge.search("请帮我写一首诗", 5).empty());
    EXPECT_TRUE(knowledge.search("what is the weather", 5).empty());
}

TEST(FWIKnowledgeBaseTest, LoadsOnlyAllowlistedBoundedRegularFiles) {
    TemporaryCorpus corpus;
    corpus.write("fwi_knowledge/allowed.md", "# 梯度\nFWI 梯度。\n");
    corpus.write("fwi_knowledge/program.py", "print('not knowledge')\n");
    corpus.write("other/private.md", "# 不应加载\nsecret\n");
    corpus.write("fwi_models/model.json", "{\"name\":\"Marmousi\"}\n");
    corpus.write("fwi_models/not_json.md", "# 不应加载\n");
    corpus.write(
        "fwi_knowledge/oversized.md",
        std::string(KnowledgeBase::kMaxDocumentBytes + 1U, 'x'));

    const auto outside_file = corpus.outside / "outside.md";
    {
        std::ofstream output(outside_file);
        output << "# 外部文件\n不得读取\n";
    }
    std::filesystem::create_symlink(
        outside_file, corpus.root / "fwi_knowledge/escape.md");

    KnowledgeBase knowledge;
    ASSERT_TRUE(knowledge.load(corpus.root.string()));
    EXPECT_EQ(knowledge.get_document_count(), 2U);
    EXPECT_FALSE(knowledge.read("fwi_knowledge/allowed.md").empty());
    EXPECT_TRUE(knowledge.read("../outside.md").empty());
    EXPECT_TRUE(knowledge.read(outside_file.string()).empty());
    EXPECT_TRUE(knowledge.read("fwi_knowledge/escape.md").empty());
}

TEST(FWIKnowledgeBaseTest, ReloadReplacesRatherThanDuplicatesCorpus) {
    TemporaryCorpus corpus;
    seed_theory_documents(corpus);
    KnowledgeBase knowledge;
    ASSERT_TRUE(knowledge.load(corpus.root.string()));
    const auto initial_count = knowledge.get_document_count();
    ASSERT_GT(initial_count, 0U);

    ASSERT_TRUE(knowledge.load(corpus.root.string()));
    EXPECT_EQ(knowledge.get_document_count(), initial_count);
}

TEST(FWIKnowledgeBaseTest, RetrievesFromCheckedInFWICorpus) {
    std::vector<std::filesystem::path> candidates = {
        std::filesystem::current_path() / "resources",
        std::filesystem::current_path().parent_path() / "resources"
    };
    const std::filesystem::path source_file(__FILE__);
    if (source_file.is_absolute()) {
        candidates.push_back(source_file.parent_path().parent_path() / "resources");
    }

    std::filesystem::path resources;
    for (const auto& candidate : candidates) {
        std::error_code ec;
        if (std::filesystem::is_directory(candidate / "fwi_knowledge", ec)) {
            resources = candidate;
            break;
        }
    }
    ASSERT_FALSE(resources.empty());

    KnowledgeBase knowledge;
    ASSERT_TRUE(knowledge.load(resources.string()));
    EXPECT_GE(knowledge.get_document_count(), 7U);

    const auto cycle = knowledge.search("解释 cycle skipping 的半周期判据", 3);
    ASSERT_FALSE(cycle.empty());
    EXPECT_EQ(cycle.front().path, "fwi_knowledge/cycle_skipping.md");

    const auto adjoint = knowledge.search("伴随状态法为什么能高效计算梯度？", 3);
    ASSERT_FALSE(adjoint.empty());
    EXPECT_EQ(adjoint.front().path, "fwi_knowledge/adjoint_state.md");
    EXPECT_TRUE(knowledge.search("附近有什么餐厅？", 3).empty());
}

}  // namespace
