/**
 * @file error_mapper.h
 * @brief A2A error code to RPC status code mapper
 * 
 * Requirements: 8.4, 10.1
 */

#pragma once

#include <a2a/core/error_code.hpp>
#include <grpcpp/grpcpp.h>
#include <string>

namespace agent_rpc {
namespace a2a_adapter {

/**
 * @brief Maps A2A error codes to gRPC status codes
 */
class ErrorMapper {
public:
    /**
     * @brief Map A2A error code to gRPC status code
     * @param a2a_code The A2A error code
     * @return Corresponding gRPC status code
     */
    static grpc::StatusCode mapToGrpcStatus(a2a::ErrorCode a2a_code);
    
    /**
     * @brief Create a gRPC Status from A2A error
     * @param a2a_code The A2A error code
     * @param message Optional error message
     * @return gRPC Status object
     */
    static grpc::Status createGrpcStatus(
        a2a::ErrorCode a2a_code,
        const std::string& message = "");
    
    /**
     * @brief Get error description for A2A error code
     * @param a2a_code The A2A error code
     * @return Human-readable error description
     */
    static std::string getErrorDescription(a2a::ErrorCode a2a_code);
    
    /**
     * @brief Map integer error code to gRPC status
     * @param error_code Integer error code
     * @return Corresponding gRPC status code
     */
    static grpc::StatusCode mapIntToGrpcStatus(int32_t error_code);
};

} // namespace a2a_adapter
} // namespace agent_rpc
