if(NOT DEFINED WEB_INDEX_PATH)
    message(FATAL_ERROR "WEB_INDEX_PATH is required")
endif()

if(NOT DEFINED WEB_SERVER_PATH)
    message(FATAL_ERROR "WEB_SERVER_PATH is required")
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
