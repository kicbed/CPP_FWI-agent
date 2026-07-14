#pragma once

#include <algorithm>
#include <cctype>
#include <string>

namespace agent_rpc::examples {

// Local inference is intentionally limited to an unambiguous loopback HTTP
// URL.  This parser is deliberately smaller than a general URL parser: no
// credentials, fragments, query strings, alternate IP spellings, redirects,
// or implicit ports are accepted.
inline bool is_strict_loopback_http_url(const std::string& url) {
    if (url.empty() || std::any_of(url.begin(), url.end(), [](unsigned char c) {
            return std::iscntrl(c) != 0 || std::isspace(c) != 0 || c == '\\';
        }) || url.find_first_of("@?#") != std::string::npos) {
        return false;
    }

    std::string remainder;
    constexpr const char* ipv4_prefix = "http://127.0.0.1:";
    constexpr const char* localhost_prefix = "http://localhost:";
    if (url.rfind(ipv4_prefix, 0) == 0) {
        remainder = url.substr(std::char_traits<char>::length(ipv4_prefix));
    } else if (url.rfind(localhost_prefix, 0) == 0) {
        remainder = url.substr(
            std::char_traits<char>::length(localhost_prefix));
    } else {
        return false;
    }

    const std::size_t slash = remainder.find('/');
    if (slash == std::string::npos || slash == 0U ||
        slash + 1U >= remainder.size() ||
        (slash + 1U < remainder.size() && remainder[slash + 1U] == '/')) {
        return false;
    }
    const std::string port = remainder.substr(0, slash);
    if (port.size() > 5U ||
        !std::all_of(port.begin(), port.end(), [](unsigned char c) {
            return std::isdigit(c) != 0;
        })) {
        return false;
    }

    unsigned long value = 0;
    for (unsigned char c : port) value = value * 10U + (c - '0');
    return value >= 1U && value <= 65535U;
}

}  // namespace agent_rpc::examples
