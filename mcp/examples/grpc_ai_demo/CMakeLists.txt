# gRPC AI Demo - 核心 RPC 示例
# 
# 这是项目的主要测试用例：
# - grpc_server: gRPC 服务端，集成 AI 模型
# - grpc_client: gRPC 客户端，发送查询请求

# gRPC Server
add_executable(grpc_server
    grpc_server_main.cpp
)

target_include_directories(grpc_server PRIVATE
    ${CMAKE_SOURCE_DIR}/server/include
    ${CMAKE_SOURCE_DIR}/client/include
    ${CMAKE_SOURCE_DIR}/common/include
    ${CMAKE_SOURCE_DIR}/proto
    ${CMAKE_BINARY_DIR}/proto
    /usr/include/jsoncpp
)

target_link_libraries(grpc_server PRIVATE
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    jsoncpp
    pthread
)

# gRPC Client
add_executable(grpc_client
    grpc_client_main.cpp
)

target_include_directories(grpc_client PRIVATE
    ${CMAKE_SOURCE_DIR}/client/include
    ${CMAKE_SOURCE_DIR}/common/include
    ${CMAKE_SOURCE_DIR}/proto
    ${CMAKE_BINARY_DIR}/proto
)

target_link_libraries(grpc_client PRIVATE
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    pthread
)

# 安装目标
install(TARGETS grpc_server grpc_client
    RUNTIME DESTINATION bin
)
