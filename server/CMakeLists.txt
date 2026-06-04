# 服务器模块
cmake_minimum_required(VERSION 3.15)

# 源文件列表 (库)
set(SERVER_SOURCES
    src/rpc_server.cpp
    src/agent_service.cpp
    src/ai_query_service.cpp
)

# 创建静态库
add_library(agent_rpc_server STATIC ${SERVER_SOURCES})

# ============================================================================
# RPC Server 可执行文件
# ============================================================================
add_executable(rpc_server src/main.cpp)
target_link_libraries(rpc_server
    agent_rpc_server
    agent_rpc_common
    agent_rpc_registry
    a2a_adapter
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    pthread
    uuid
    CURL::libcurl
)

# 链接依赖
target_link_libraries(agent_rpc_server 
    agent_rpc_common
    agent_rpc_registry
    a2a_adapter
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    grpc++_reflection
    pthread
    CURL::libcurl
)

# 包含目录
target_include_directories(agent_rpc_server PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

# 编译选项
target_compile_options(agent_rpc_server PRIVATE
    -Wall
    -Wextra
    -std=c++17
)

# 安装规则
install(TARGETS agent_rpc_server
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
)

install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/agent_rpc
    DESTINATION include
    FILES_MATCHING PATTERN "*.h"
)
