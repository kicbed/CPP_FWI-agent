cmake_minimum_required(VERSION 3.20)

# MCP模块配置
set(MCP_SOURCES
    src/mcp_client.cpp
    src/mcp_tool_manager.cpp
    src/mcp_agent_integration.cpp
    # RAG-MCP 源文件
    src/rag/embedding_service.cpp
    src/rag/embedding_cache.cpp
    src/rag/vector_index.cpp
    src/rag/tool_retriever.cpp
    src/rag/tool_validator.cpp
)

# 创建MCP库
add_library(agent_rpc_mcp STATIC ${MCP_SOURCES})

# 设置包含目录
target_include_directories(agent_rpc_mcp PUBLIC
    include
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# 查找jsoncpp
find_package(PkgConfig REQUIRED)
pkg_check_modules(JSONCPP REQUIRED jsoncpp)

# 链接依赖库
target_link_libraries(agent_rpc_mcp PRIVATE
    agent_rpc_common
    ${JSONCPP_LIBRARIES}
    Threads::Threads
    CURL::libcurl
)

target_include_directories(agent_rpc_mcp PRIVATE ${JSONCPP_INCLUDE_DIRS})

# 设置C++标准
target_compile_features(agent_rpc_mcp PRIVATE cxx_std_20)

# 设置编译选项
if(CMAKE_COMPILER_IS_GNUCC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(agent_rpc_mcp PRIVATE -Wall -Wextra -Wpedantic)
    if(CMAKE_BUILD_TYPE STREQUAL "Debug")
        target_compile_options(agent_rpc_mcp PRIVATE -g -O0)
    else()
        target_compile_options(agent_rpc_mcp PRIVATE -O2 -DNDEBUG)
    endif()
endif()

# 安装规则
install(TARGETS agent_rpc_mcp
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
)

install(DIRECTORY include/agent_rpc/mcp/
    DESTINATION include/agent_rpc/mcp
    FILES_MATCHING PATTERN "*.h"
)


