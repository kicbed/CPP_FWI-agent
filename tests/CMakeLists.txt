# 查找Google Test
find_package(GTest REQUIRED)

add_executable(test_rpc_framework test_rpc_framework.cpp)
target_link_libraries(test_rpc_framework
    agent_rpc_common
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_rpc_framework PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
)

# 序列化测试
add_executable(test_serialization test_serialization.cpp)

# 链接库
target_link_libraries(test_serialization 
    agent_rpc_common
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    GTest::gtest
    GTest::gtest_main
    pthread
    uuid
)

# 包含目录
target_include_directories(test_serialization PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../include
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

# A2A集成测试
add_executable(test_a2a_integration test_a2a_integration.cpp)

target_link_libraries(test_a2a_integration 
    a2a
    GTest::gtest
    GTest::gtest_main
    pthread
    CURL::libcurl
)

target_include_directories(test_a2a_integration PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
)

# 添加测试
enable_testing()
add_test(NAME RpcFrameworkTest COMMAND test_rpc_framework)
add_test(NAME SerializationTest COMMAND test_serialization)
add_test(NAME A2AIntegrationTest COMMAND test_a2a_integration)

add_executable(test_code_agent_registration test_code_agent_registration.cpp)
target_link_libraries(test_code_agent_registration
    a2a
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_code_agent_registration PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
)
add_test(NAME CodeAgentRegistrationTest COMMAND test_code_agent_registration)

add_executable(test_experiment_planner_registration test_experiment_planner_registration.cpp)
target_link_libraries(test_experiment_planner_registration
    a2a
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_experiment_planner_registration PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
)
add_test(NAME ExperimentPlannerRegistrationTest COMMAND test_experiment_planner_registration)

if(NOT TARGET ai_code_agent)
    message(FATAL_ERROR "ai_code_agent target is required for Code Agent MVP")
endif()
add_test(
    NAME CodeAgentExecutableTargetTest
    COMMAND ${CMAKE_COMMAND}
        -DEXECUTABLE_PATH=$<TARGET_FILE:ai_code_agent>
        -P ${CMAKE_CURRENT_SOURCE_DIR}/check_executable.cmake
)

if(NOT TARGET ai_experiment_planner_agent)
    message(FATAL_ERROR "ai_experiment_planner_agent target is required for v0.2 Experiment Planner skeleton")
endif()
add_test(
    NAME ExperimentPlannerExecutableTargetTest
    COMMAND ${CMAKE_COMMAND}
        -DEXECUTABLE_PATH=$<TARGET_FILE:ai_experiment_planner_agent>
        -P ${CMAKE_CURRENT_SOURCE_DIR}/check_executable.cmake
)

add_executable(test_code_agent_tools test_code_agent_tools.cpp)
target_link_libraries(test_code_agent_tools
    ai_code_agent_tools
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_code_agent_tools PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../examples/ai_orchestrator
)
add_test(NAME CodeAgentToolsTest COMMAND test_code_agent_tools)

add_executable(test_algorithm_card test_algorithm_card.cpp)
target_link_libraries(test_algorithm_card
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_algorithm_card PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME AlgorithmCardTest COMMAND test_algorithm_card)

add_executable(test_algorithm_registry test_algorithm_registry.cpp)
target_link_libraries(test_algorithm_registry
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_algorithm_registry PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME AlgorithmRegistryTest COMMAND test_algorithm_registry)

add_executable(test_experiment_spec test_experiment_spec.cpp)
target_link_libraries(test_experiment_spec
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_experiment_spec PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME ExperimentSpecTest COMMAND test_experiment_spec)

add_executable(test_research_knowledge test_research_knowledge.cpp)
target_link_libraries(test_research_knowledge
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_research_knowledge PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME ResearchKnowledgeTest COMMAND test_research_knowledge)

add_executable(test_planner_context test_planner_context.cpp)
target_link_libraries(test_planner_context
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_planner_context PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME PlannerContextTest COMMAND test_planner_context)

add_executable(test_planner_answer test_planner_answer.cpp)
target_link_libraries(test_planner_answer
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_planner_answer PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME PlannerAnswerTest COMMAND test_planner_answer)

add_executable(test_lab_code_adapter test_lab_code_adapter.cpp)
target_link_libraries(test_lab_code_adapter
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_lab_code_adapter PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME LabCodeAdapterTest COMMAND test_lab_code_adapter)

add_executable(test_server_job test_server_job.cpp)
target_link_libraries(test_server_job
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_server_job PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME ServerJobTest COMMAND test_server_job)

add_executable(test_single_server_backend test_single_server_backend.cpp)
target_link_libraries(test_single_server_backend
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_single_server_backend PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME SingleServerBackendTest COMMAND test_single_server_backend)

add_executable(test_safe_operations test_safe_operations.cpp)
target_link_libraries(test_safe_operations
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_safe_operations PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME SafeOperationsTest COMMAND test_safe_operations)

add_executable(test_single_server_lifecycle test_single_server_lifecycle.cpp)
target_link_libraries(test_single_server_lifecycle
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_single_server_lifecycle PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME SingleServerLifecycleTest COMMAND test_single_server_lifecycle)

add_executable(test_workspace_planner test_workspace_planner.cpp)
target_link_libraries(test_workspace_planner
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_workspace_planner PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME WorkspacePlannerTest COMMAND test_workspace_planner)

add_executable(test_approved_template_run_packet test_approved_template_run_packet.cpp)
target_link_libraries(test_approved_template_run_packet
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_approved_template_run_packet PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME ApprovedTemplateRunPacketTest COMMAND test_approved_template_run_packet)

add_executable(test_internal_sanity_runner test_internal_sanity_runner.cpp)
target_link_libraries(test_internal_sanity_runner
    agent_rpc_research
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_internal_sanity_runner PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../research/include
)
add_test(NAME InternalSanityRunnerTest COMMAND test_internal_sanity_runner)

add_test(
    NAME WebBrandingTest
    COMMAND ${CMAKE_COMMAND}
        -DWEB_INDEX_PATH=${CMAKE_CURRENT_SOURCE_DIR}/../web/index.html
        -DWEB_SERVER_PATH=${CMAKE_CURRENT_SOURCE_DIR}/../web/serve.py
        -DV05_REPORT_PATH=${CMAKE_CURRENT_SOURCE_DIR}/../docs/upgrade/test-report-v0.5.md
        -P ${CMAKE_CURRENT_SOURCE_DIR}/check_web_branding.cmake
)

# Proto序列化往返属性测试
# **Feature: a2a-integration, Property 11: Protobuf Serialization Round-Trip**
# **Validates: Requirements 11.5**
find_package(rapidcheck REQUIRED)

add_executable(test_proto_roundtrip test_proto_roundtrip.cpp)

target_link_libraries(test_proto_roundtrip 
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    rapidcheck
    GTest::gtest
    GTest::gtest_main
    pthread
)

target_include_directories(test_proto_roundtrip PRIVATE
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

add_test(NAME ProtoRoundTripTest COMMAND test_proto_roundtrip)

# A2A适配器属性测试
# **Feature: a2a-integration, Property 1, 7, 10**
# **Validates: Requirements 5.1, 5.5, 8.1-8.4, 9.5, 10.1**
add_executable(test_adapter_properties test_adapter_properties.cpp)

target_link_libraries(test_adapter_properties 
    a2a_adapter
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    rapidcheck
    GTest::gtest
    GTest::gtest_main
    pthread
    CURL::libcurl
)

target_include_directories(test_adapter_properties PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a_adapter/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

add_test(NAME AdapterPropertiesTest COMMAND test_adapter_properties)

# Task Manager属性测试
# **Feature: a2a-integration, Property 2, 3, 6**
# **Validates: Requirements 3.2, 3.5, 4.2, 5.5, 6.1-6.6**
add_executable(test_task_manager_properties test_task_manager_properties.cpp)

target_link_libraries(test_task_manager_properties 
    a2a_adapter
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    rapidcheck
    GTest::gtest
    GTest::gtest_main
    pthread
    CURL::libcurl
)

target_include_directories(test_task_manager_properties PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a_adapter/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

add_test(NAME TaskManagerPropertiesTest COMMAND test_task_manager_properties)

# Agent Router属性测试
# **Feature: a2a-integration, Property 4, 8**
# **Validates: Requirements 2.3, 3.4, 4.5, 10.3**
add_executable(test_agent_router_properties test_agent_router_properties.cpp)

target_link_libraries(test_agent_router_properties 
    orchestrator
    a2a_adapter
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    rapidcheck
    GTest::gtest
    GTest::gtest_main
    pthread
    CURL::libcurl
)

target_include_directories(test_agent_router_properties PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../orchestrator/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a_adapter/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

add_test(NAME AgentRouterPropertiesTest COMMAND test_agent_router_properties)

# 安装测试
install(TARGETS test_serialization
    RUNTIME DESTINATION bin
)


# AI Query集成测试
# **Feature: a2a-integration, Task 17: 集成测试**
# **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 9.5, 10.2, 10.5**
add_executable(test_ai_query_integration test_ai_query_integration.cpp)

target_link_libraries(test_ai_query_integration 
    agent_rpc_server
    agent_rpc_client
    a2a_adapter
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    rapidcheck
    GTest::gtest
    GTest::gtest_main
    pthread
    uuid
    CURL::libcurl
)

target_include_directories(test_ai_query_integration PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../server/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../client/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a_adapter/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

add_test(NAME AIQueryIntegrationTest COMMAND test_ai_query_integration)

# MCP Agent集成测试
# **Feature: a2a-integration, Task 20: MCP 集成测试**
# **Validates: Requirements 12.1, 12.3, 12.5**
add_executable(test_mcp_integration test_mcp_integration.cpp)

target_link_libraries(test_mcp_integration 
    agent_rpc_mcp
    agent_rpc_common
    rapidcheck
    GTest::gtest
    GTest::gtest_main
    pthread
)

target_include_directories(test_mcp_integration PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../mcp/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
)

add_test(NAME MCPIntegrationTest COMMAND test_mcp_integration)

# Deterministic routing for the fixed-whitelist Deepwave FWI MCP tools.
add_executable(test_fwi_tool_routing test_fwi_tool_routing.cpp)
target_link_libraries(test_fwi_tool_routing
    agent_rpc_mcp
    agent_rpc_common
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_fwi_tool_routing PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../orchestrator/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../mcp/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
)
add_test(NAME FWIToolRoutingTest COMMAND test_fwi_tool_routing)

# Bounded and role-aware conversation context construction.
add_executable(test_context_window test_context_window.cpp)
target_link_libraries(test_context_window
    orchestrator
    a2a
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_context_window PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../orchestrator/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
)
add_test(NAME ContextWindowTest COMMAND test_context_window)

add_executable(test_specialist_context test_specialist_context.cpp)
target_link_libraries(test_specialist_context
    a2a
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_specialist_context PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../examples/ai_orchestrator
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
)
add_test(NAME SpecialistContextTest COMMAND test_specialist_context)

# Strict specialist LLM provider/key/endpoint mapping. The test is pure and
# deliberately does not inspect API-key environment variables.
add_executable(test_llm_runtime_config test_llm_runtime_config.cpp)
target_link_libraries(test_llm_runtime_config
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_llm_runtime_config PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../examples/ai_orchestrator
    ${CMAKE_CURRENT_SOURCE_DIR}/../orchestrator/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
)
add_test(NAME LLMRuntimeConfigTest COMMAND test_llm_runtime_config)

# Embedding cache persistence must not follow symlinks or write relative to an
# untrusted launch directory.
add_executable(test_vector_store_security test_vector_store_security.cpp)
target_link_libraries(test_vector_store_security
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_vector_store_security PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../orchestrator/include
)
add_test(NAME VectorStoreSecurityTest COMMAND test_vector_store_security)

add_test(
    NAME RuntimeSecretIsolationTest
    COMMAND bash ${CMAKE_CURRENT_SOURCE_DIR}/test_runtime_secret_isolation.sh
)

# Pure local FWI document loading and relevance ranking. This test performs no
# LLM or embedding network request.
add_executable(test_fwi_knowledge_base test_fwi_knowledge_base.cpp)
target_link_libraries(test_fwi_knowledge_base
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_fwi_knowledge_base PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../orchestrator/include
)
add_test(NAME FWIKnowledgeBaseTest COMMAND test_fwi_knowledge_base)

# RAG-MCP 属性测试
# **Feature: rag-mcp, Property 1-8**
# **Validates: Requirements 1.3, 3.2, 3.3, 3.4, 4.1, 4.3, 7.2, 7.3**
add_executable(test_rag_mcp_properties test_rag_mcp_properties.cpp)

target_link_libraries(test_rag_mcp_properties 
    agent_rpc_mcp
    agent_rpc_common
    rapidcheck
    GTest::gtest
    GTest::gtest_main
    pthread
    CURL::libcurl
)

target_include_directories(test_rag_mcp_properties PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../mcp/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
)

add_test(NAME RAGMCPPropertiesTest COMMAND test_rag_mcp_properties)

add_executable(test_service_registry test_service_registry.cpp)
target_link_libraries(test_service_registry
    agent_rpc_registry
    agent_rpc_common
    GTest::gtest
    GTest::gtest_main
    pthread
)
target_include_directories(test_service_registry PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../registry/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
)
add_test(NAME ServiceRegistryTest COMMAND test_service_registry)

add_executable(test_agent_communication test_agent_communication.cpp)
target_link_libraries(test_agent_communication
    agent_rpc_server
    agent_rpc_client
    agent_rpc_registry
    agent_rpc_common
    GTest::gtest
    GTest::gtest_main
    pthread
    uuid
    CURL::libcurl
)
target_include_directories(test_agent_communication PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../server/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../client/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../registry/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../common/include
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)
add_test(NAME AgentCommunicationIntegrationTest COMMAND test_agent_communication)
