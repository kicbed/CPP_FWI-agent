cmake_minimum_required(VERSION 3.15)

set(RESEARCH_SOURCES
    src/algorithm_card.cpp
    src/algorithm_listing_tool.cpp
    src/algorithm_registry.cpp
    src/dry_run_backend.cpp
    src/experiment_spec.cpp
    src/job_backend.cpp
    src/job_spec.cpp
    src/lab_code_adapter.cpp
    src/planner_answer.cpp
    src/planner_context.cpp
    src/research_knowledge.cpp
    src/server_job.cpp
    src/single_server_backend.cpp
)

add_library(agent_rpc_research STATIC ${RESEARCH_SOURCES})

target_include_directories(agent_rpc_research PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(agent_rpc_research PUBLIC
    agent_rpc_common
)

target_compile_features(agent_rpc_research PUBLIC cxx_std_17)
