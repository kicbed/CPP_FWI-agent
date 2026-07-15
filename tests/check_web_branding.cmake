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
    "Deepwave 二维常密度声学 FWI 实验工作台"
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
    "id=\"fwiQuickActions\""
    "id=\"experimentHistory\""
    "id=\"workbenchInspector\""
    "Deepwave 2D Acoustic FWI"
    "最近 FWI 任务"
    "当前开放能力"
    "一键提交"
    "marmousi_94_288"
    "服务器 Draft / Plan 确认卡"
    "批准前不会进入运行队列"
    "批准运行"
    "放弃草稿"
    "Demo CPU"
    "Demo CUDA"
    "自定义迭代"
    "1–10000 次"
    "运行 500 次迭代的 FWI"
    "Smoke CUDA"
    "Smoke CPU"
    "gRPC 桥"
    "./start.sh --grpc"
    "grpcAvailable"
    "setGrpcAvailability"
    "health.transport === 'grpc'"
    "已自动切回 HTTP"
    "processFwiPayloadFromAnswer"
    "随 Orchestrator 按需调用"
    "statusRegistry"
    "statusMcp"
    "statusEmbedding"
    "statusCodeAgent"
    "statusPlannerAgent"
    "Planner Agent")
    if(NOT web_index MATCHES "${required}")
        message(FATAL_ERROR "Missing required Lab Workbench UI element or helper: ${required}")
    endif()
endforeach()

foreach(forbidden
    "CUDA-MPI FWI"
    "marmousi2 dry-run"
    "queued draft"
    "dry-run research state"
    "dry_run: true"
    "id=\"algorithmList\""
    "Route Trace"
    "Tool Calls"
    "AlgorithmCard"
    "ExperimentSpec"
    "JobSpec")
    if(web_index MATCHES "${forbidden}")
        message(FATAL_ERROR "Web UI still exposes placeholder or unavailable state: ${forbidden}")
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
