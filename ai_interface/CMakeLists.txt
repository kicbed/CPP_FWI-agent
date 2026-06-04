cmake_minimum_required(VERSION 3.20)

# AI接口模块配置
set(AI_INTERFACE_SOURCES
    src/ai_interface.cpp
)

# 创建AI接口库
add_library(agent_rpc_ai_interface STATIC ${AI_INTERFACE_SOURCES})

# 设置包含目录
target_include_directories(agent_rpc_ai_interface PUBLIC
    include
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# 链接依赖库
target_link_libraries(agent_rpc_ai_interface PRIVATE
    agent_rpc_common
    agent_rpc_mcp
    jsoncpp
    Threads::Threads
)

# 设置C++标准
target_compile_features(agent_rpc_ai_interface PRIVATE cxx_std_20)

# 设置编译选项
if(CMAKE_COMPILER_IS_GNUCC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(agent_rpc_ai_interface PRIVATE -Wall -Wextra -Wpedantic)
    if(CMAKE_BUILD_TYPE STREQUAL "Debug")
        target_compile_options(agent_rpc_ai_interface PRIVATE -g -O0)
    else()
        target_compile_options(agent_rpc_ai_interface PRIVATE -O2 -DNDEBUG)
    endif()
endif()

# 安装规则
install(TARGETS agent_rpc_ai_interface
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
)

install(DIRECTORY include/agent_rpc/ai/
    DESTINATION include/agent_rpc/ai
    FILES_MATCHING PATTERN "*.h"
)


