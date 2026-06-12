#include <agent_rpc/research/research_knowledge.h>

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <string>

using agent_rpc::research::ResearchKnowledgeBase;
using agent_rpc::research::ResearchKnowledgeNote;
using agent_rpc::research::json;

namespace {

std::filesystem::path repo_root() {
    return std::filesystem::path(__FILE__).parent_path().parent_path();
}

}  // namespace

TEST(ResearchKnowledgeNoteTest, ParsesAndValidatesStructuredNote) {
    const json value = {
        {"id", "algorithm.multi_scale_fwi"},
        {"title", "Multi-scale FWI Parameter Guidance"},
        {"note_type", "algorithm"},
        {"summary", "Use staged frequency bands to reduce cycle skipping risk."},
        {"methods", {"multi-scale-fwi", "fwi"}},
        {"datasets", {"marmousi"}},
        {"assumptions", {"initial model is kinematically plausible"}},
        {"parameters", {"frequency_band", "niter"}},
        {"failure_modes", {"cycle_skipping"}},
        {"parameter_advice", {{"frequency_band", "start from the lowest reliable band"}}},
        {"tags", {"fwi", "inversion"}},
        {"source", "local v0.3 seed note"}
    };

    const auto note = ResearchKnowledgeNote::from_json(value);

    EXPECT_TRUE(note.validate().empty());
    EXPECT_EQ(note.id, "algorithm.multi_scale_fwi");
    EXPECT_EQ(note.note_type, "algorithm");
    EXPECT_EQ(note.parameter_advice.at("frequency_band"), "start from the lowest reliable band");
}

TEST(ResearchKnowledgeBaseTest, LoadsSeedNotesFromTypedDirectories) {
    ResearchKnowledgeBase knowledge;
    std::string error;

    ASSERT_TRUE(knowledge.load_from_directory(
        repo_root() / "resources" / "research_knowledge", &error)) << error;

    EXPECT_GE(knowledge.notes().size(), 4u);
    ASSERT_NE(knowledge.find_by_id("algorithm.multi_scale_fwi"), nullptr);
    EXPECT_EQ(knowledge.find_by_id("algorithm.multi_scale_fwi")->note_type, "algorithm");

    const auto paper_notes = knowledge.filter_by_note_type("paper");
    EXPECT_FALSE(paper_notes.empty());

    const auto method_notes = knowledge.filter_by_method("multi-scale-fwi");
    EXPECT_FALSE(method_notes.empty());
}

TEST(ResearchKnowledgeBaseTest, RetrievesAdviceByFailureModeAndMethod) {
    ResearchKnowledgeBase knowledge;
    std::string error;
    ASSERT_TRUE(knowledge.load_from_directory(
        repo_root() / "resources" / "research_knowledge", &error)) << error;

    const auto cycle_skipping_notes = knowledge.find_by_failure_mode("cycle_skipping");
    ASSERT_FALSE(cycle_skipping_notes.empty());
    EXPECT_EQ(cycle_skipping_notes[0].note_type, "failure_case");

    const auto advice = knowledge.parameter_advice_for("multi-scale-fwi", "frequency_band");
    ASSERT_FALSE(advice.empty());
    EXPECT_NE(advice[0].find("lowest reliable"), std::string::npos);
}

TEST(ResearchKnowledgeBaseTest, RejectsInvalidNotesWithClearError) {
    const auto temp_root = std::filesystem::temp_directory_path() /
        "agent_rpc_research_knowledge_invalid_test";
    std::filesystem::remove_all(temp_root);
    std::filesystem::create_directories(temp_root / "papers");

    std::ofstream invalid(temp_root / "papers" / "missing_id.json");
    invalid << R"({
        "title": "Invalid Note",
        "note_type": "paper",
        "summary": "This note is missing a stable id."
    })";
    invalid.close();

    ResearchKnowledgeBase knowledge;
    std::string error;
    EXPECT_FALSE(knowledge.load_from_directory(temp_root, &error));
    EXPECT_NE(error.find("missing_id.json"), std::string::npos);
    EXPECT_NE(error.find("id is required"), std::string::npos);

    std::filesystem::remove_all(temp_root);
}
