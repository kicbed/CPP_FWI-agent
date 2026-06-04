# 公共组件库
cmake_minimum_required(VERSION 3.15)

# 源文件列表
set(COMMON_SOURCES
    src/logger.cpp
    src/serializer.cpp
    src/message_converter.cpp
    src/metrics.cpp
    src/circuit_breaker.cpp
    src/load_balancer.cpp
)

# 创建静态库
add_library(agent_rpc_common STATIC ${COMMON_SOURCES})

# 链接依赖
target_link_libraries(agent_rpc_common 
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    pthread
    uuid
    curl
    jsoncpp
)

# 包含目录
target_include_directories(agent_rpc_common PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

# 编译选项
target_compile_options(agent_rpc_common PRIVATE
    -Wall
    -Wextra
    -std=c++17
)

# 安装规则
install(TARGETS agent_rpc_common
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
)

install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/agent_rpc
    DESTINATION include
    FILES_MATCHING PATTERN "*.h"
)
