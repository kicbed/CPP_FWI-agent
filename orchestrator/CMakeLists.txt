# Orchestrator Module CMakeLists.txt
# Task 8.1, 11.6: Orchestrator模块编译配置
# Requirements: 3.1

cmake_minimum_required(VERSION 3.15)

# Source files
set(ORCHESTRATOR_SOURCES
    src/agent_router.cpp
    src/context_window.cpp
    src/memory_manager.cpp
)

# Header files
set(ORCHESTRATOR_HEADERS
    include/agent_rpc/orchestrator/agent_info.h
    include/agent_rpc/orchestrator/agent_router.h
    include/agent_rpc/orchestrator/request_context.h
    include/agent_rpc/orchestrator/trace_logger.h
    include/agent_rpc/orchestrator/config.h
    include/agent_rpc/orchestrator/context_window.h
    include/agent_rpc/orchestrator/memory_manager.h
)

# Create library
add_library(orchestrator ${ORCHESTRATOR_SOURCES} ${ORCHESTRATOR_HEADERS})

target_include_directories(orchestrator
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
        ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
        ${CMAKE_CURRENT_SOURCE_DIR}/../a2a_adapter/include
        ${CMAKE_BINARY_DIR}/proto
)

target_link_libraries(orchestrator
    PUBLIC
        a2a
        a2a_adapter
        ${GRPC_LIBRARIES}
        ${PROTOBUF_LIBRARIES}
        hiredis
)

# Compiler options
if(NOT MSVC)
    target_compile_options(orchestrator PRIVATE -Wall -Wextra)
endif()

# Export target
set_target_properties(orchestrator PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)
