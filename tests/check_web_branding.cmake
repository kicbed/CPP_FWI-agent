if(NOT DEFINED WEB_INDEX_PATH)
    message(FATAL_ERROR "WEB_INDEX_PATH is required")
endif()

if(NOT DEFINED WEB_SERVER_PATH)
    message(FATAL_ERROR "WEB_SERVER_PATH is required")
endif()

if(NOT DEFINED V05_REPORT_PATH)
    message(FATAL_ERROR "V05_REPORT_PATH is required")
endif()

file(READ "${WEB_INDEX_PATH}" web_index)
file(READ "${WEB_SERVER_PATH}" web_server)

foreach(required
    "<title>Lab Agent Workbench</title>"
    "Lab Agent Workbench"
    "Seismic Research Workspace"
    "FWI-first research computing workbench"
    "Powered by Lab Agent Workbench")
    if(NOT web_index MATCHES "${required}")
        message(FATAL_ERROR "Missing required Web UI branding text: ${required}")
    endif()
endforeach()

if(web_index MATCHES "AI Agent Orchestrator")
    message(FATAL_ERROR "Web UI still uses old AI Agent Orchestrator branding")
endif()

if(NOT web_server MATCHES "Lab Agent Workbench HTTP Server")
    message(FATAL_ERROR "web/serve.py must describe the Lab Agent Workbench HTTP server")
endif()

if(NOT web_server MATCHES "Lab Agent Workbench .*已启动")
    message(FATAL_ERROR "web/serve.py startup banner must use Lab Agent Workbench branding")
endif()

foreach(required
    "id=\"algorithmList\""
    "id=\"experimentHistory\""
    "id=\"workbenchInspector\""
    "Route Trace"
    "Tool Calls"
    "AlgorithmCard"
    "ExperimentSpec"
    "JobSpec"
    "Parameter Table"
    "dry_run: true"
    "statusRegistry"
    "statusMcp"
    "statusEmbedding"
    "statusCodeAgent"
    "statusPlannerAgent"
    "Planner Agent"
    "fwi-cuda-mpi"
    "frequency-extrapolation"
    "poststack-inversion"
    "renderAlgorithmList"
    "selectAlgorithm"
    "renderExperimentSpec"
    "renderJobSpec"
    "extractExperimentSpec"
    "extractJobSpec"
    "updateInspectorFromAnswer")
    if(NOT web_index MATCHES "${required}")
        message(FATAL_ERROR "Missing required Lab Workbench UI element or helper: ${required}")
    endif()
endforeach()

if(NOT EXISTS "${V05_REPORT_PATH}")
    message(FATAL_ERROR "v0.5 test report is required: ${V05_REPORT_PATH}")
endif()

file(READ "${V05_REPORT_PATH}" v05_report)
foreach(required
    "# v0.5 Lab Workbench UI Test Report"
    "## 1. 解决的问题"
    "## 2. 实现方式"
    "## 3. 关键文件/测试/资源"
    "## 4. 安全或产品边界"
    "## 5. 调试或 TDD 证据"
    "## 6. 面试怎么讲")
    if(NOT v05_report MATCHES "${required}")
        message(FATAL_ERROR "Missing required v0.5 report section: ${required}")
    endif()
endforeach()
