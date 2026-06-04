/**
 * @file test_task_manager_properties.cpp
 * @brief Property-based tests for Task Manager
 * 
 * Task 6.2, 6.4, 6.6: Property tests for task management
 * 
 * **Feature: a2a-integration**
 * **Property 2: Task ID Uniqueness**
 * **Property 3: Task State Machine Consistency**
 * **Property 6: Context ID Preservation**
 * **Validates: Requirements 3.2, 3.5, 4.2, 5.5, 6.1-6.6**
 */

#include <gtest/gtest.h>
#include <rapidcheck.h>
#include <rapidcheck/gtest.h>

#include "agent_rpc/a2a_adapter/task_manager_wrapper.h"
#include <a2a/core/types.hpp>
#include <a2a/core/exception.hpp>
#include <a2a/models/agent_message.hpp>

#include <unordered_set>
#include <vector>
#include <string>
#include <thread>
#include <future>

using namespace agent_rpc::a2a_adapter;

// ============================================================================
// Test Fixtures
// ============================================================================

class TaskManagerPropertyTest : public ::testing::Test {
protected:
    void SetUp() override {
        wrapper_ = std::make_unique<TaskManagerWrapper>();
        ASSERT_TRUE(wrapper_->initialize());
    }
    
    void TearDown() override {
        wrapper_->shutdown();
    }
    
    std::unique_ptr<TaskManagerWrapper> wrapper_;
};

// ============================================================================
// Helper Generators
// ============================================================================

namespace rc {

// Generator for valid context IDs (ASCII alphanumeric)
Gen<std::string> genContextId() {
    return gen::map(
        gen::container<std::string>(gen::inRange('a', 'z')),
        [](std::string s) {
            if (s.empty()) return std::string("ctx-default");
            return "ctx-" + s;
        }
    );
}

// Generator for task count (reasonable range)
Gen<int> genTaskCount() {
    return gen::inRange(1, 50);
}

// Generator for message content
Gen<std::string> genMessageContent() {
    return gen::map(
        gen::container<std::string>(gen::inRange('a', 'z')),
        [](std::string s) {
            if (s.empty()) return std::string("test message");
            return s;
        }
    );
}

// Generator for valid state transitions from Submitted
Gen<a2a::TaskState> genValidTransitionFromSubmitted() {
    return gen::element(
        a2a::TaskState::Running,
        a2a::TaskState::Canceled,
        a2a::TaskState::Rejected
    );
}

// Generator for valid state transitions from Running
Gen<a2a::TaskState> genValidTransitionFromRunning() {
    return gen::element(
        a2a::TaskState::Completed,
        a2a::TaskState::Failed,
        a2a::TaskState::Canceled
    );
}

} // namespace rc

// ============================================================================
// Property 2: Task ID Uniqueness
// **Validates: Requirements 3.2, 4.2, 6.1**
// ============================================================================

/**
 * Property 2.1: All created task IDs are unique
 * For any sequence of task creations, no two tasks should have the same ID.
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, TaskIdsAreUnique, ()) {
    int task_count = *rc::genTaskCount();
    
    std::unordered_set<std::string> task_ids;
    
    for (int i = 0; i < task_count; ++i) {
        auto task = wrapper_->createTask();
        
        // Check that this ID hasn't been seen before
        RC_ASSERT(task_ids.find(task.id()) == task_ids.end());
        
        task_ids.insert(task.id());
    }
    
    // Verify we created the expected number of unique tasks
    RC_ASSERT(task_ids.size() == static_cast<size_t>(task_count));
}

/**
 * Property 2.2: Task IDs are unique across different contexts
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, TaskIdsUniqueAcrossContexts, ()) {
    auto context1 = *rc::genContextId();
    auto context2 = *rc::genContextId();
    int tasks_per_context = *rc::gen::inRange(1, 20);
    
    std::unordered_set<std::string> all_task_ids;
    
    // Create tasks in context 1
    for (int i = 0; i < tasks_per_context; ++i) {
        auto task = wrapper_->createTask(context1);
        RC_ASSERT(all_task_ids.find(task.id()) == all_task_ids.end());
        all_task_ids.insert(task.id());
    }
    
    // Create tasks in context 2
    for (int i = 0; i < tasks_per_context; ++i) {
        auto task = wrapper_->createTask(context2);
        RC_ASSERT(all_task_ids.find(task.id()) == all_task_ids.end());
        all_task_ids.insert(task.id());
    }
    
    RC_ASSERT(all_task_ids.size() == static_cast<size_t>(tasks_per_context * 2));
}

/**
 * Property 2.3: Concurrent task creation maintains uniqueness
 */
TEST_F(TaskManagerPropertyTest, ConcurrentTaskCreationUniqueness) {
    const int num_threads = 4;
    const int tasks_per_thread = 25;
    
    std::vector<std::future<std::vector<std::string>>> futures;
    
    for (int t = 0; t < num_threads; ++t) {
        futures.push_back(std::async(std::launch::async, [this, tasks_per_thread]() {
            std::vector<std::string> ids;
            for (int i = 0; i < tasks_per_thread; ++i) {
                auto task = wrapper_->createTask();
                ids.push_back(task.id());
            }
            return ids;
        }));
    }
    
    std::unordered_set<std::string> all_ids;
    for (auto& future : futures) {
        auto ids = future.get();
        for (const auto& id : ids) {
            EXPECT_TRUE(all_ids.find(id) == all_ids.end()) 
                << "Duplicate task ID found: " << id;
            all_ids.insert(id);
        }
    }
    
    EXPECT_EQ(all_ids.size(), static_cast<size_t>(num_threads * tasks_per_thread));
}

// ============================================================================
// Property 3: Task State Machine Consistency
// **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
// ============================================================================

/**
 * Property 3.1: Valid state transitions succeed
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, ValidStateTransitionsSucceed, ()) {
    auto task = wrapper_->createTask();
    
    // Initial state should be Submitted
    RC_ASSERT(wrapper_->getTaskState(task.id()) == a2a::TaskState::Submitted);
    
    // Transition to Running (valid from Submitted)
    RC_ASSERT(wrapper_->updateTaskState(task.id(), a2a::TaskState::Running));
    RC_ASSERT(wrapper_->getTaskState(task.id()) == a2a::TaskState::Running);
    
    // Transition to a terminal state (valid from Running)
    auto terminal_state = *rc::genValidTransitionFromRunning();
    RC_ASSERT(wrapper_->updateTaskState(task.id(), terminal_state));
    RC_ASSERT(wrapper_->getTaskState(task.id()) == terminal_state);
}

/**
 * Property 3.2: Invalid state transitions throw exception
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, InvalidStateTransitionsThrow, ()) {
    auto task = wrapper_->createTask();
    
    // Try invalid transition: Submitted -> Completed (must go through Running)
    RC_ASSERT_THROWS_AS(
        wrapper_->updateTaskState(task.id(), a2a::TaskState::Completed),
        std::invalid_argument
    );
    
    // State should remain Submitted
    RC_ASSERT(wrapper_->getTaskState(task.id()) == a2a::TaskState::Submitted);
}

/**
 * Property 3.3: Terminal states cannot transition
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, TerminalStatesCannotTransition, ()) {
    auto task = wrapper_->createTask();
    
    // Move to Running then to Completed
    wrapper_->updateTaskState(task.id(), a2a::TaskState::Running);
    wrapper_->updateTaskState(task.id(), a2a::TaskState::Completed);
    
    // Try to transition from Completed (terminal) to any other state
    RC_ASSERT_THROWS_AS(
        wrapper_->updateTaskState(task.id(), a2a::TaskState::Running),
        std::invalid_argument
    );
    
    RC_ASSERT_THROWS_AS(
        wrapper_->updateTaskState(task.id(), a2a::TaskState::Submitted),
        std::invalid_argument
    );
    
    // State should remain Completed
    RC_ASSERT(wrapper_->getTaskState(task.id()) == a2a::TaskState::Completed);
}

/**
 * Property 3.4: State machine follows valid paths
 * Success path: Submitted -> Running -> Completed
 * Failure path: Submitted -> Running -> Failed
 * Cancel path: Submitted -> Running -> Canceled (or Submitted -> Canceled)
 */
TEST_F(TaskManagerPropertyTest, StateMachineValidPaths) {
    // Success path
    {
        auto task = wrapper_->createTask();
        EXPECT_EQ(wrapper_->getTaskState(task.id()), a2a::TaskState::Submitted);
        EXPECT_TRUE(wrapper_->updateTaskState(task.id(), a2a::TaskState::Running));
        EXPECT_TRUE(wrapper_->updateTaskState(task.id(), a2a::TaskState::Completed));
        EXPECT_EQ(wrapper_->getTaskState(task.id()), a2a::TaskState::Completed);
    }
    
    // Failure path
    {
        auto task = wrapper_->createTask();
        EXPECT_TRUE(wrapper_->updateTaskState(task.id(), a2a::TaskState::Running));
        EXPECT_TRUE(wrapper_->updateTaskState(task.id(), a2a::TaskState::Failed));
        EXPECT_EQ(wrapper_->getTaskState(task.id()), a2a::TaskState::Failed);
    }
    
    // Cancel from Running
    {
        auto task = wrapper_->createTask();
        EXPECT_TRUE(wrapper_->updateTaskState(task.id(), a2a::TaskState::Running));
        EXPECT_TRUE(wrapper_->updateTaskState(task.id(), a2a::TaskState::Canceled));
        EXPECT_EQ(wrapper_->getTaskState(task.id()), a2a::TaskState::Canceled);
    }
    
    // Cancel from Submitted (direct)
    {
        auto task = wrapper_->createTask();
        EXPECT_TRUE(wrapper_->updateTaskState(task.id(), a2a::TaskState::Canceled));
        EXPECT_EQ(wrapper_->getTaskState(task.id()), a2a::TaskState::Canceled);
    }
}

/**
 * Property 3.5: TaskStateValidator correctly identifies valid transitions
 */
TEST_F(TaskManagerPropertyTest, StateValidatorCorrectness) {
    // From Submitted
    EXPECT_TRUE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Submitted, a2a::TaskState::Running));
    EXPECT_TRUE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Submitted, a2a::TaskState::Canceled));
    EXPECT_FALSE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Submitted, a2a::TaskState::Completed));
    EXPECT_FALSE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Submitted, a2a::TaskState::Failed));
    
    // From Running
    EXPECT_TRUE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Running, a2a::TaskState::Completed));
    EXPECT_TRUE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Running, a2a::TaskState::Failed));
    EXPECT_TRUE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Running, a2a::TaskState::Canceled));
    EXPECT_FALSE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Running, a2a::TaskState::Submitted));
    
    // From terminal states (no valid transitions)
    EXPECT_FALSE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Completed, a2a::TaskState::Running));
    EXPECT_FALSE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Failed, a2a::TaskState::Running));
    EXPECT_FALSE(TaskStateValidator::isValidTransition(
        a2a::TaskState::Canceled, a2a::TaskState::Running));
}

// ============================================================================
// Property 6: Context ID Preservation
// **Validates: Requirements 3.5, 5.5, 6.6**
// ============================================================================

/**
 * Property 6.1: Context ID is preserved across task operations
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, ContextIdPreserved, ()) {
    auto context_id = *rc::genContextId();
    
    auto task = wrapper_->createTask(context_id);
    
    // Context ID should be preserved
    RC_ASSERT(task.context_id() == context_id);
    
    // Retrieve task and verify context ID
    auto retrieved = wrapper_->getTask(task.id());
    RC_ASSERT(retrieved.context_id() == context_id);
}

/**
 * Property 6.2: Tasks can be retrieved by context ID
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, TasksRetrievableByContext, ()) {
    auto context_id = *rc::genContextId();
    int task_count = *rc::gen::inRange(1, 10);
    
    std::vector<std::string> created_task_ids;
    
    // Create multiple tasks with same context
    for (int i = 0; i < task_count; ++i) {
        auto task = wrapper_->createTask(context_id);
        created_task_ids.push_back(task.id());
    }
    
    // Retrieve tasks by context
    auto retrieved_ids = wrapper_->getTasksByContext(context_id);
    
    // All created tasks should be retrievable
    RC_ASSERT(retrieved_ids.size() == created_task_ids.size());
    
    for (const auto& id : created_task_ids) {
        RC_ASSERT(std::find(retrieved_ids.begin(), retrieved_ids.end(), id) 
                  != retrieved_ids.end());
    }
}

/**
 * Property 6.3: Message history is preserved by context
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, MessageHistoryPreservedByContext, ()) {
    auto context_id = *rc::genContextId();
    int message_count = *rc::gen::inRange(1, 10);
    
    auto task = wrapper_->createTask(context_id);
    
    std::vector<std::string> sent_messages;
    
    // Add messages to task
    for (int i = 0; i < message_count; ++i) {
        auto content = *rc::genMessageContent();
        sent_messages.push_back(content);
        
        auto message = a2a::AgentMessage::create()
            .with_context_id(context_id)
            .with_task_id(task.id())
            .with_role(a2a::MessageRole::User)
            .with_text(content);
        
        wrapper_->addMessage(task.id(), message);
    }
    
    // Retrieve history
    auto history = wrapper_->getHistory(task.id());
    
    // All messages should be in history
    RC_ASSERT(history.size() == sent_messages.size());
    
    // Messages should be in chronological order
    for (size_t i = 0; i < history.size(); ++i) {
        RC_ASSERT(history[i].get_text() == sent_messages[i]);
    }
}

/**
 * Property 6.4: History max_length limit is respected
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, HistoryMaxLengthRespected, ()) {
    auto context_id = *rc::genContextId();
    int total_messages = *rc::gen::inRange(5, 20);
    int max_length = *rc::gen::inRange(1, total_messages);
    
    auto task = wrapper_->createTask(context_id);
    
    // Add messages
    for (int i = 0; i < total_messages; ++i) {
        auto message = a2a::AgentMessage::create()
            .with_context_id(context_id)
            .with_task_id(task.id())
            .with_text("Message " + std::to_string(i));
        
        wrapper_->addMessage(task.id(), message);
    }
    
    // Retrieve with limit
    auto history = wrapper_->getHistory(task.id(), max_length);
    
    // Should return at most max_length messages
    RC_ASSERT(history.size() <= static_cast<size_t>(max_length));
    
    // Should return the most recent messages
    if (history.size() == static_cast<size_t>(max_length)) {
        // Last message should be the most recent
        int expected_last_index = total_messages - 1;
        RC_ASSERT(history.back().get_text() == 
                  "Message " + std::to_string(expected_last_index));
    }
}

/**
 * Property 6.5: Different contexts have isolated histories
 */
RC_GTEST_FIXTURE_PROP(TaskManagerPropertyTest, ContextHistoriesIsolated, ()) {
    auto context1 = "ctx-isolated-1";
    auto context2 = "ctx-isolated-2";
    
    auto task1 = wrapper_->createTask(context1);
    auto task2 = wrapper_->createTask(context2);
    
    // Add messages to task1
    auto msg1 = a2a::AgentMessage::create()
        .with_context_id(context1)
        .with_task_id(task1.id())
        .with_text("Message for context 1");
    wrapper_->addMessage(task1.id(), msg1);
    
    // Add messages to task2
    auto msg2 = a2a::AgentMessage::create()
        .with_context_id(context2)
        .with_task_id(task2.id())
        .with_text("Message for context 2");
    wrapper_->addMessage(task2.id(), msg2);
    
    // Histories should be isolated
    auto history1 = wrapper_->getHistory(task1.id());
    auto history2 = wrapper_->getHistory(task2.id());
    
    RC_ASSERT(history1.size() == 1);
    RC_ASSERT(history2.size() == 1);
    RC_ASSERT(history1[0].get_text() == "Message for context 1");
    RC_ASSERT(history2[0].get_text() == "Message for context 2");
}

// ============================================================================
// Additional Unit Tests
// ============================================================================

TEST_F(TaskManagerPropertyTest, InitializeAndShutdown) {
    TaskManagerWrapper wrapper;
    
    EXPECT_TRUE(wrapper.initialize());
    EXPECT_EQ(wrapper.getTotalTasksCreated(), 0u);
    
    wrapper.shutdown();
    
    // Can reinitialize after shutdown
    EXPECT_TRUE(wrapper.initialize());
}

TEST_F(TaskManagerPropertyTest, TaskNotFoundThrows) {
    EXPECT_THROW(wrapper_->getTask("non-existent-task"), a2a::A2AException);
}

TEST_F(TaskManagerPropertyTest, TaskExistsCheck) {
    auto task = wrapper_->createTask();
    
    EXPECT_TRUE(wrapper_->taskExists(task.id()));
    EXPECT_FALSE(wrapper_->taskExists("non-existent-task"));
}

TEST_F(TaskManagerPropertyTest, CancelTaskFromSubmitted) {
    auto task = wrapper_->createTask();
    
    auto cancelled = wrapper_->cancelTask(task.id());
    
    EXPECT_EQ(cancelled.status().state(), a2a::TaskState::Canceled);
}

TEST_F(TaskManagerPropertyTest, StateChangeCallbackInvoked) {
    std::vector<std::tuple<std::string, a2a::TaskState, a2a::TaskState>> transitions;
    
    wrapper_->setStateChangeCallback(
        [&transitions](const std::string& task_id, 
                       a2a::TaskState old_state, 
                       a2a::TaskState new_state) {
            transitions.emplace_back(task_id, old_state, new_state);
        }
    );
    
    auto task = wrapper_->createTask();
    wrapper_->updateTaskState(task.id(), a2a::TaskState::Running);
    wrapper_->updateTaskState(task.id(), a2a::TaskState::Completed);
    
    ASSERT_EQ(transitions.size(), 2u);
    
    EXPECT_EQ(std::get<0>(transitions[0]), task.id());
    EXPECT_EQ(std::get<1>(transitions[0]), a2a::TaskState::Submitted);
    EXPECT_EQ(std::get<2>(transitions[0]), a2a::TaskState::Running);
    
    EXPECT_EQ(std::get<0>(transitions[1]), task.id());
    EXPECT_EQ(std::get<1>(transitions[1]), a2a::TaskState::Running);
    EXPECT_EQ(std::get<2>(transitions[1]), a2a::TaskState::Completed);
}

TEST_F(TaskManagerPropertyTest, ActiveTaskCount) {
    EXPECT_EQ(wrapper_->getActiveTaskCount(), 0u);
    
    auto task1 = wrapper_->createTask();
    auto task2 = wrapper_->createTask();
    
    EXPECT_EQ(wrapper_->getActiveTaskCount(), 2u);
    
    wrapper_->updateTaskState(task1.id(), a2a::TaskState::Running);
    wrapper_->updateTaskState(task1.id(), a2a::TaskState::Completed);
    
    EXPECT_EQ(wrapper_->getActiveTaskCount(), 1u);
    
    wrapper_->updateTaskState(task2.id(), a2a::TaskState::Canceled);
    
    EXPECT_EQ(wrapper_->getActiveTaskCount(), 0u);
}

// ============================================================================
// Main
// ============================================================================

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
