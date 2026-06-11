if(NOT DEFINED EXECUTABLE_PATH)
    message(FATAL_ERROR "EXECUTABLE_PATH is required")
endif()

if(NOT EXISTS "${EXECUTABLE_PATH}")
    message(FATAL_ERROR "Expected executable does not exist: ${EXECUTABLE_PATH}")
endif()

execute_process(
    COMMAND "${EXECUTABLE_PATH}"
    RESULT_VARIABLE executable_result
    OUTPUT_QUIET
    ERROR_QUIET
)

if(NOT executable_result EQUAL 1)
    message(FATAL_ERROR "Expected executable usage path to exit 1, got ${executable_result}: ${EXECUTABLE_PATH}")
endif()
