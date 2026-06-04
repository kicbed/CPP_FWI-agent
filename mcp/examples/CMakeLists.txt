# 示例程序
cmake_minimum_required(VERSION 3.15)

# ============================================================================
# 注意: rpc_server 和 rpc_client 主程序在 server/ 和 client/ 目录中
# 编译后位于: build/server/rpc_server 和 build/client/rpc_client
# ============================================================================

# 序列化示例 (暂时禁用，需要完整的服务实现)
# add_executable(serialization_example serialization_example.cpp)
# target_link_libraries(serialization_example 
#     agent_rpc_server
#     agent_rpc_client
#     agent_rpc_common
#     ${GRPC_LIBRARIES}
#     ${PROTOBUF_LIBRARIES}
#     pthread
#     uuid
# )

# AI-MCP集成示例 (暂时禁用，ai_interface模块有设计问题)
# add_executable(ai_mcp_integration_example ai_mcp_integration_example.cpp)
# target_link_libraries(ai_mcp_integration_example 
#     agent_rpc_ai_interface
#     agent_rpc_mcp
#     agent_rpc_common
#     jsoncpp
#     pthread
# )

# RPC-MCP集成测试 (暂时禁用，ai_interface模块有设计问题)
# add_executable(rpc_mcp_integration_test rpc_mcp_integration_test.cpp)
# target_link_libraries(rpc_mcp_integration_test 
#     agent_rpc_server
#     agent_rpc_ai_interface
#     agent_rpc_mcp
#     agent_rpc_common
#     ${GRPC_LIBRARIES}
#     ${PROTOBUF_LIBRARIES}
#     jsoncpp
#     pthread
#     uuid
# )

# AI Orchestrator 示例 (基于 a2a-cpp multi_agent_demo)
add_subdirectory(ai_orchestrator)

# gRPC AI Demo - 核心 RPC 示例 (Client/Server 直接通信)
add_subdirectory(grpc_ai_demo)

# RAG-MCP 示例
add_executable(rag_mcp_example rag_mcp_example.cpp)
target_link_libraries(rag_mcp_example 
    agent_rpc_mcp
    agent_rpc_common
    jsoncpp
    pthread
)
target_include_directories(rag_mcp_example PRIVATE
    ${CMAKE_SOURCE_DIR}/mcp/include
    ${CMAKE_SOURCE_DIR}/common/include
)

# 安装示例 (AI Orchestrator 示例)
# install(TARGETS server_example client_example serialization_example
#     RUNTIME DESTINATION bin
# )