# A2A Adapter Layer CMakeLists.txt
# Requirements: 8.5

cmake_minimum_required(VERSION 3.15)

# Source files
set(A2A_ADAPTER_SOURCES
    src/request_adapter.cpp
    src/response_adapter.cpp
    src/error_mapper.cpp
    src/a2a_adapter.cpp
    src/task_manager_wrapper.cpp
    src/a2a_metrics.cpp
)

# Header files
set(A2A_ADAPTER_HEADERS
    include/agent_rpc/a2a_adapter/a2a_config.h
    include/agent_rpc/a2a_adapter/request_adapter.h
    include/agent_rpc/a2a_adapter/response_adapter.h
    include/agent_rpc/a2a_adapter/error_mapper.h
    include/agent_rpc/a2a_adapter/a2a_adapter.h
    include/agent_rpc/a2a_adapter/task_manager_wrapper.h
    include/agent_rpc/a2a_adapter/a2a_metrics.h
    include/agent_rpc/a2a_adapter/retry_policy.h
)

# Create library
add_library(a2a_adapter ${A2A_ADAPTER_SOURCES} ${A2A_ADAPTER_HEADERS})

target_include_directories(a2a_adapter
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/include
        ${CMAKE_CURRENT_SOURCE_DIR}/../a2a/third_party
        ${CMAKE_BINARY_DIR}/proto
)

target_link_libraries(a2a_adapter
    PUBLIC
        a2a
        proto_lib
        ${GRPC_LIBRARIES}
        ${PROTOBUF_LIBRARIES}
)

# Compiler options
if(NOT MSVC)
    target_compile_options(a2a_adapter PRIVATE -Wall -Wextra)
endif()

# Export target
set_target_properties(a2a_adapter PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)
