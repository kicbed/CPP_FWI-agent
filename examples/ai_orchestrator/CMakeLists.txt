# AI Orchestrator Example
cmake_minimum_required(VERSION 3.15)

find_package(PkgConfig REQUIRED)
pkg_check_modules(HIREDIS hiredis)

# Orchestrator
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
target_link_libraries(ai_orchestrator a2a a2a_adapter orchestrator agent_rpc_mcp proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads ${HIREDIS_LIBRARIES})

# Math Agent
add_executable(ai_math_agent math_agent_main.cpp ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp)
target_include_directories(ai_math_agent PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party ${CMAKE_CURRENT_SOURCE_DIR}/../../mcp/include ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include ${CMAKE_BINARY_DIR}/proto ${HIREDIS_INCLUDE_DIRS})
target_link_libraries(ai_math_agent a2a agent_rpc_mcp proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads ${HIREDIS_LIBRARIES})

# Registry Server
add_executable(ai_registry_server registry_server_main.cpp)
target_include_directories(ai_registry_server PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party)
target_link_libraries(ai_registry_server a2a CURL::libcurl Threads::Threads)

# FWI Theory Agent
add_executable(ai_fwi_theory_agent fwi_theory_agent_main.cpp ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp)
target_include_directories(ai_fwi_theory_agent PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party ${CMAKE_CURRENT_SOURCE_DIR}/../../orchestrator/include ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include ${CMAKE_BINARY_DIR}/proto ${HIREDIS_INCLUDE_DIRS})
target_link_libraries(ai_fwi_theory_agent a2a orchestrator proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads ${HIREDIS_LIBRARIES})

# FWI Teaching Agent
add_executable(ai_fwi_teaching_agent fwi_teaching_agent_main.cpp ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp)
target_include_directories(ai_fwi_teaching_agent PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include ${CMAKE_BINARY_DIR}/proto ${HIREDIS_INCLUDE_DIRS})
target_link_libraries(ai_fwi_teaching_agent a2a proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads ${HIREDIS_LIBRARIES})

# General Research Agent
add_executable(ai_general_research_agent general_research_agent_main.cpp ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp)
target_include_directories(ai_general_research_agent PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include ${CMAKE_BINARY_DIR}/proto ${HIREDIS_INCLUDE_DIRS})
target_link_libraries(ai_general_research_agent a2a proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads ${HIREDIS_LIBRARIES})

# Code Agent
add_library(ai_code_agent_tools STATIC code_agent_tools.cpp)
target_include_directories(ai_code_agent_tools PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_features(ai_code_agent_tools PUBLIC cxx_std_17)

add_executable(ai_code_agent code_agent_main.cpp ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp)
target_include_directories(ai_code_agent PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include ${CMAKE_BINARY_DIR}/proto ${HIREDIS_INCLUDE_DIRS})
target_link_libraries(ai_code_agent ai_code_agent_tools a2a proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads ${HIREDIS_LIBRARIES})

# Experiment Planner Agent
add_executable(ai_experiment_planner_agent experiment_planner_agent_main.cpp ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp)
target_include_directories(ai_experiment_planner_agent PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include ${CMAKE_CURRENT_SOURCE_DIR}/../../research/include ${CMAKE_BINARY_DIR}/proto ${HIREDIS_INCLUDE_DIRS})
target_link_libraries(ai_experiment_planner_agent agent_rpc_research a2a proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads ${HIREDIS_LIBRARIES})

# Interactive Client (new version with conversation list)
add_executable(ai_client ai_client.cpp)
target_include_directories(ai_client PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a_adapter/include ${CMAKE_BINARY_DIR}/proto)
target_link_libraries(ai_client a2a a2a_adapter proto_lib ${GRPC_LIBRARIES} ${PROTOBUF_LIBRARIES} CURL::libcurl Threads::Threads)
