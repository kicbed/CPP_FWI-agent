# 客户端模块
cmake_minimum_required(VERSION 3.15)

# 源文件列表 (库)
set(CLIENT_SOURCES
    src/rpc_client.cpp
    src/ai_query_client.cpp
)

# 创建静态库
add_library(agent_rpc_client STATIC ${CLIENT_SOURCES})

# ============================================================================
# RPC Client 可执行文件
# ============================================================================
add_executable(rpc_client src/main.cpp)
target_link_libraries(rpc_client
    agent_rpc_client
    agent_rpc_common
    agent_rpc_registry
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    pthread
    uuid
)

# 链接依赖
target_link_libraries(agent_rpc_client 
    agent_rpc_common
    agent_rpc_registry
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    pthread
    uuid
)

# 包含目录
target_include_directories(agent_rpc_client PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

# 编译选项
target_compile_options(agent_rpc_client PRIVATE
    -Wall
    -Wextra
    -std=c++17
)

# 安装规则
install(TARGETS agent_rpc_client
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
)

install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/agent_rpc
    DESTINATION include
    FILES_MATCHING PATTERN "*.h"
)
