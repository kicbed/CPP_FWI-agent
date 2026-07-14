# AI Orchestrator Example
# 基于 a2a-cpp/examples/multi_agent_demo 的集成示例

message(FATAL_ERROR
    "mcp/examples/ai_orchestrator is a retired legacy snapshot and must not be built. "
    "Use examples/ai_orchestrator through the repository root CMake project.")

cmake_minimum_required(VERSION 3.15)

# 查找 hiredis (Redis 客户端)
find_package(PkgConfig REQUIRED)
pkg_check_modules(HIREDIS hiredis)

# Orchestrator 可执行文件
add_executable(ai_orchestrator
    orchestrator_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp
)

target_include_directories(ai_orchestrator PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a_adapter/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../orchestrator/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../mcp/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include
    ${CMAKE_BINARY_DIR}/proto
    ${HIREDIS_INCLUDE_DIRS}
)

target_link_libraries(ai_orchestrator
    a2a
    a2a_adapter
    orchestrator
    agent_rpc_mcp
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    Threads::Threads
    ${HIREDIS_LIBRARIES}
)

# Math Agent 可执行文件
add_executable(ai_math_agent
    math_agent_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp
)

target_include_directories(ai_math_agent PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../../mcp/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include
    ${CMAKE_BINARY_DIR}/proto
    ${HIREDIS_INCLUDE_DIRS}
)

target_link_libraries(ai_math_agent
    a2a
    agent_rpc_mcp
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    Threads::Threads
    ${HIREDIS_LIBRARIES}
)

# Registry Server 可执行文件
add_executable(ai_registry_server
    registry_server_main.cpp
)

target_include_directories(ai_registry_server PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
)

target_link_libraries(ai_registry_server
    a2a
    CURL::libcurl
    Threads::Threads
)

# FWI Theory Agent 可执行文件
add_executable(ai_fwi_theory_agent
    fwi_theory_agent_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp
)

target_include_directories(ai_fwi_theory_agent PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include
    ${CMAKE_BINARY_DIR}/proto
    ${HIREDIS_INCLUDE_DIRS}
)

target_link_libraries(ai_fwi_theory_agent
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    Threads::Threads
    ${HIREDIS_LIBRARIES}
)

# FWI Teaching Agent 可执行文件
add_executable(ai_fwi_teaching_agent
    fwi_teaching_agent_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp
)

target_include_directories(ai_fwi_teaching_agent PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include
    ${CMAKE_BINARY_DIR}/proto
    ${HIREDIS_INCLUDE_DIRS}
)

target_link_libraries(ai_fwi_teaching_agent
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    Threads::Threads
    ${HIREDIS_LIBRARIES}
)

# General Research Agent 可执行文件
add_executable(ai_general_research_agent
    general_research_agent_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp
)

target_include_directories(ai_general_research_agent PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include
    ${CMAKE_BINARY_DIR}/proto
    ${HIREDIS_INCLUDE_DIRS}
)

target_link_libraries(ai_general_research_agent
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    Threads::Threads
    ${HIREDIS_LIBRARIES}
)

# Interactive Client 可执行文件
add_executable(ai_client
    client_main.cpp
)

target_include_directories(ai_client PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a_adapter/include
    ${CMAKE_BINARY_DIR}/proto
)

target_link_libraries(ai_client
    a2a
    a2a_adapter
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    Threads::Threads
)
