# 注册中心模块
cmake_minimum_required(VERSION 3.15)

# 源文件列表
set(REGISTRY_SOURCES
    src/service_registry.cpp
)

# 创建静态库
add_library(agent_rpc_registry STATIC ${REGISTRY_SOURCES})

# 链接依赖
target_link_libraries(agent_rpc_registry 
    agent_rpc_common
    curl
    jsoncpp
    pthread
)

# 包含目录
target_include_directories(agent_rpc_registry PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_BINARY_DIR}/../proto
)

# 编译选项
target_compile_options(agent_rpc_registry PRIVATE
    -Wall
    -Wextra
    -std=c++17
)

# 安装规则
install(TARGETS agent_rpc_registry
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
)

install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/agent_rpc
    DESTINATION include
    FILES_MATCHING PATTERN "*.h"
)
