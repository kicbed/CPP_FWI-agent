cmake_minimum_required(VERSION 3.10)

if(MINGW)
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -static-libgcc -static-libstdc++")
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -static-libgcc")
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static")
    link_libraries(pthread)
endif()

# Calculator plugin library
add_library(calculator SHARED
    ${PROJECT_SOURCE_DIR}/plugins/calculator/Calculator.cpp
)

# For Linux/Unix, ensure we're building with PIC
if(UNIX)
    set_target_properties(calculator PROPERTIES POSITION_INDEPENDENT_CODE ON)
endif()

# Link libraries
if(WIN32)
    target_link_libraries(calculator PRIVATE "-static -static-libgcc -static-libstdc++ -lpthread")
else()
    find_package(Threads REQUIRED)
    target_link_libraries(calculator PRIVATE Threads::Threads)
endif()

target_compile_definitions(calculator PRIVATE CALCULATOR_EXPORTS)
target_include_directories(calculator PRIVATE 
    ${PROJECT_SOURCE_DIR}/include 
    ${PROJECT_SOURCE_DIR}/src/interface
)
