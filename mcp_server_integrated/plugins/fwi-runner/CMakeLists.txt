cmake_minimum_required(VERSION 3.10)

add_library(fwi-runner SHARED
    ${CMAKE_CURRENT_SOURCE_DIR}/FWIRunner.cpp
)

set_target_properties(fwi-runner PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)

get_filename_component(FWI_PROJECT_ROOT_DIR "${CMAKE_SOURCE_DIR}/.." ABSOLUTE)
target_compile_definitions(fwi-runner PRIVATE
    FWI_PROJECT_ROOT="${FWI_PROJECT_ROOT_DIR}"
)

target_include_directories(fwi-runner PRIVATE
    ${PROJECT_SOURCE_DIR}/include
    ${PROJECT_SOURCE_DIR}/src/interface
)

find_package(Threads REQUIRED)
target_link_libraries(fwi-runner PRIVATE Threads::Threads)

if(BUILD_TESTING)
    add_executable(fwi-runner-test
        ${CMAKE_CURRENT_SOURCE_DIR}/FWIRunnerTest.cpp
    )
    target_include_directories(fwi-runner-test PRIVATE
        ${PROJECT_SOURCE_DIR}/include
        ${PROJECT_SOURCE_DIR}/src/interface
    )
    target_link_libraries(fwi-runner-test PRIVATE fwi-runner Threads::Threads)
    add_test(NAME FWIRunnerPluginTest COMMAND fwi-runner-test)
endif()
