# A2A Protocol Library CMakeLists.txt
# Integrated from /home/ubuntu/a2a-cpp
# Preserving original a2a:: namespace

cmake_minimum_required(VERSION 3.15)

# Find dependencies
find_package(CURL REQUIRED)
find_package(Threads REQUIRED)

# Include directories
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/third_party)

# Source files
set(A2A_SOURCES
    # Core
    src/core/exception.cpp
    src/core/jsonrpc_request.cpp
    src/core/jsonrpc_response.cpp
    src/core/http_client.cpp
    
    # Models
    src/models/message_part.cpp
    src/models/agent_message.cpp
    src/models/task_status.cpp
    src/models/artifact.cpp
    src/models/agent_task.cpp
    src/models/agent_card.cpp
    src/models/message_send_params.cpp
    
    # Client
    src/client/card_resolver.cpp
    src/client/a2a_client.cpp
    
    # Server
    src/server/memory_task_store.cpp
    src/server/task_manager.cpp
)

# Header files
set(A2A_HEADERS
    # Core
    include/a2a/core/types.hpp
    include/a2a/core/error_code.hpp
    include/a2a/core/exception.hpp
    include/a2a/core/a2a_methods.hpp
    include/a2a/core/jsonrpc_request.hpp
    include/a2a/core/jsonrpc_response.hpp
    include/a2a/core/http_client.hpp
    
    # Models
    include/a2a/models/message_part.hpp
    include/a2a/models/agent_message.hpp
    include/a2a/models/task_status.hpp
    include/a2a/models/artifact.hpp
    include/a2a/models/agent_task.hpp
    include/a2a/models/agent_card.hpp
    include/a2a/models/message_send_params.hpp
    include/a2a/models/a2a_response.hpp
    
    # Client
    include/a2a/client/card_resolver.hpp
    include/a2a/client/a2a_client.hpp
    
    # Server
    include/a2a/server/task_store.hpp
    include/a2a/server/memory_task_store.hpp
    include/a2a/server/task_manager.hpp
)

# Create library
add_library(a2a ${A2A_SOURCES} ${A2A_HEADERS})

target_include_directories(a2a
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(a2a
    PUBLIC
        CURL::libcurl
        Threads::Threads
)

# Compiler warnings
if(MSVC)
    target_compile_options(a2a PRIVATE /W4)
else()
    target_compile_options(a2a PRIVATE -Wall -Wextra -Wpedantic)
endif()

# Export target for use by other modules
set_target_properties(a2a PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)
