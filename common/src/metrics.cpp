#include "agent_rpc/common/metrics.h"
#include "agent_rpc/common/logger.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <numeric>
#include <sstream>

namespace agent_rpc {
namespace common {

namespace {

void atomicAdd(std::atomic<double>& target, double delta) {
    double current = target.load();
    while (!target.compare_exchange_weak(current, current + delta)) {
    }
}

std::vector<double> defaultHistogramBuckets() {
    return {0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0,
            250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0,
            std::numeric_limits<double>::infinity()};
}

std::vector<double> defaultSummaryQuantiles() {
    return {0.5, 0.9, 0.95, 0.99};
}

std::string formatLabels(const std::map<std::string, std::string>& labels) {
    if (labels.empty()) {
        return "";
    }

    std::ostringstream oss;
    oss << "{";
    bool first = true;
    for (const auto& pair : labels) {
        if (!first) {
            oss << ",";
        }
        oss << pair.first << "=\"" << pair.second << "\"";
        first = false;
    }
    oss << "}";
    return oss.str();
}

}  // namespace

CounterMetric::CounterMetric(const std::string& name,
                             const std::string& description,
                             const std::map<std::string, std::string>& labels)
    : name_(name), description_(description), labels_(labels) {}

std::string CounterMetric::toString() const {
    std::ostringstream oss;
    oss << "# HELP " << name_ << " " << description_ << "\n";
    oss << "# TYPE " << name_ << " counter\n";
    oss << name_ << formatLabels(labels_) << " " << value_.load() << "\n";
    return oss.str();
}

void CounterMetric::increment(double value) {
    atomicAdd(value_, value);
}

void CounterMetric::set(double value) {
    value_.store(value);
}

void CounterMetric::reset() {
    value_.store(0.0);
}

GaugeMetric::GaugeMetric(const std::string& name,
                         const std::string& description,
                         const std::map<std::string, std::string>& labels)
    : name_(name), description_(description), labels_(labels) {}

std::string GaugeMetric::toString() const {
    std::ostringstream oss;
    oss << "# HELP " << name_ << " " << description_ << "\n";
    oss << "# TYPE " << name_ << " gauge\n";
    oss << name_ << formatLabels(labels_) << " " << value_.load() << "\n";
    return oss.str();
}

void GaugeMetric::set(double value) {
    value_.store(value);
}

void GaugeMetric::increment(double value) {
    atomicAdd(value_, value);
}

void GaugeMetric::decrement(double value) {
    atomicAdd(value_, -value);
}

void GaugeMetric::reset() {
    value_.store(0.0);
}

HistogramMetric::HistogramMetric(const std::string& name,
                                 const std::string& description,
                                 const std::vector<double>& buckets,
                                 const std::map<std::string, std::string>& labels)
    : name_(name), description_(description), labels_(labels) {
    const auto bucket_values = buckets.empty() ? defaultHistogramBuckets() : buckets;
    for (double bucket : bucket_values) {
        buckets_.emplace_back(bucket);
    }
}

std::string HistogramMetric::toString() const {
    std::ostringstream oss;
    oss << "# HELP " << name_ << " " << description_ << "\n";
    oss << "# TYPE " << name_ << " histogram\n";

    std::lock_guard<std::mutex> lock(buckets_mutex_);
    for (const auto& bucket : buckets_) {
        auto labels = labels_;
        labels["le"] = std::isinf(bucket.upper_bound) ? "+Inf" : std::to_string(bucket.upper_bound);
        oss << name_ << "_bucket" << formatLabels(labels)
            << " " << bucket.count.load() << "\n";
    }

    oss << name_ << "_count" << formatLabels(labels_) << " " << count_.load() << "\n";
    oss << name_ << "_sum" << formatLabels(labels_) << " " << sum_.load() << "\n";
    return oss.str();
}

void HistogramMetric::observe(double value) {
    atomicAdd(sum_, value);
    count_.fetch_add(1);

    std::lock_guard<std::mutex> lock(buckets_mutex_);
    for (auto& bucket : buckets_) {
        if (value <= bucket.upper_bound) {
            bucket.count.fetch_add(1);
        }
    }
}

std::vector<HistogramBucket> HistogramMetric::getBuckets() const {
    std::lock_guard<std::mutex> lock(buckets_mutex_);
    return buckets_;
}

void HistogramMetric::reset() {
    sum_.store(0.0);
    count_.store(0);
    std::lock_guard<std::mutex> lock(buckets_mutex_);
    for (auto& bucket : buckets_) {
        bucket.count.store(0);
    }
}

SummaryMetric::SummaryMetric(const std::string& name,
                             const std::string& description,
                             const std::vector<double>& quantiles,
                             const std::map<std::string, std::string>& labels)
    : name_(name),
      description_(description),
      labels_(labels),
      quantiles_(quantiles.empty() ? defaultSummaryQuantiles() : quantiles) {}

std::string SummaryMetric::toString() const {
    std::ostringstream oss;
    oss << "# HELP " << name_ << " " << description_ << "\n";
    oss << "# TYPE " << name_ << " summary\n";

    std::lock_guard<std::mutex> lock(values_mutex_);
    for (const auto& pair : quantile_values_) {
        auto labels = labels_;
        labels["quantile"] = std::to_string(pair.first);
        oss << name_ << formatLabels(labels) << " " << pair.second << "\n";
    }

    oss << name_ << "_count" << formatLabels(labels_) << " " << count_.load() << "\n";
    oss << name_ << "_sum" << formatLabels(labels_) << " " << sum_.load() << "\n";
    return oss.str();
}

void SummaryMetric::observe(double value) {
    atomicAdd(sum_, value);
    count_.fetch_add(1);

    std::lock_guard<std::mutex> lock(values_mutex_);
    values_.push_back(value);
    if (values_.size() > 1000) {
        values_.erase(values_.begin());
    }
    updateQuantiles();
}

std::map<double, double> SummaryMetric::getQuantiles() const {
    std::lock_guard<std::mutex> lock(values_mutex_);
    return quantile_values_;
}

void SummaryMetric::reset() {
    sum_.store(0.0);
    count_.store(0);
    std::lock_guard<std::mutex> lock(values_mutex_);
    values_.clear();
    quantile_values_.clear();
}

void SummaryMetric::updateQuantiles() {
    if (values_.empty()) {
        quantile_values_.clear();
        return;
    }

    auto sorted_values = values_;
    std::sort(sorted_values.begin(), sorted_values.end());

    quantile_values_.clear();
    for (double quantile : quantiles_) {
        if (quantile < 0.0 || quantile > 1.0) {
            continue;
        }
        size_t index = static_cast<size_t>(quantile * static_cast<double>(sorted_values.size() - 1));
        quantile_values_[quantile] = sorted_values[index];
    }
}

MetricsCollector& MetricsCollector::getInstance() {
    static MetricsCollector instance;
    return instance;
}

std::shared_ptr<CounterMetric> MetricsCollector::createCounter(
    const std::string& name,
    const std::string& description,
    const std::map<std::string, std::string>& labels) {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    const auto key = generateMetricKey(name, labels);
    auto it = metrics_.find(key);
    if (it != metrics_.end()) {
        return std::static_pointer_cast<CounterMetric>(it->second);
    }
    auto metric = std::make_shared<CounterMetric>(name, description, labels);
    metrics_[key] = metric;
    return metric;
}

std::shared_ptr<GaugeMetric> MetricsCollector::createGauge(
    const std::string& name,
    const std::string& description,
    const std::map<std::string, std::string>& labels) {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    const auto key = generateMetricKey(name, labels);
    auto it = metrics_.find(key);
    if (it != metrics_.end()) {
        return std::static_pointer_cast<GaugeMetric>(it->second);
    }
    auto metric = std::make_shared<GaugeMetric>(name, description, labels);
    metrics_[key] = metric;
    return metric;
}

std::shared_ptr<HistogramMetric> MetricsCollector::createHistogram(
    const std::string& name,
    const std::string& description,
    const std::vector<double>& buckets,
    const std::map<std::string, std::string>& labels) {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    const auto key = generateMetricKey(name, labels);
    auto it = metrics_.find(key);
    if (it != metrics_.end()) {
        return std::static_pointer_cast<HistogramMetric>(it->second);
    }
    auto metric = std::make_shared<HistogramMetric>(name, description, buckets, labels);
    metrics_[key] = metric;
    return metric;
}

std::shared_ptr<SummaryMetric> MetricsCollector::createSummary(
    const std::string& name,
    const std::string& description,
    const std::vector<double>& quantiles,
    const std::map<std::string, std::string>& labels) {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    const auto key = generateMetricKey(name, labels);
    auto it = metrics_.find(key);
    if (it != metrics_.end()) {
        return std::static_pointer_cast<SummaryMetric>(it->second);
    }
    auto metric = std::make_shared<SummaryMetric>(name, description, quantiles, labels);
    metrics_[key] = metric;
    return metric;
}

std::shared_ptr<Metric> MetricsCollector::getMetric(const std::string& name) {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    for (const auto& pair : metrics_) {
        if (pair.second->getName() == name) {
            return pair.second;
        }
    }
    return nullptr;
}

std::vector<std::shared_ptr<Metric>> MetricsCollector::getAllMetrics() {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    std::vector<std::shared_ptr<Metric>> result;
    for (const auto& pair : metrics_) {
        result.push_back(pair.second);
    }
    return result;
}

void MetricsCollector::removeMetric(const std::string& name) {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    for (auto it = metrics_.begin(); it != metrics_.end();) {
        if (it->second->getName() == name) {
            it = metrics_.erase(it);
        } else {
            ++it;
        }
    }
}

std::string MetricsCollector::exportPrometheus() const {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    std::ostringstream oss;
    for (const auto& pair : metrics_) {
        oss << pair.second->toString();
    }
    return oss.str();
}

std::string MetricsCollector::exportJson() const {
    std::lock_guard<std::mutex> lock(metrics_mutex_);
    std::ostringstream oss;
    oss << "{\n  \"metrics\": [";
    bool first = true;
    for (const auto& pair : metrics_) {
        if (!first) {
            oss << ",";
        }
        oss << "\n    {"
            << "\"name\": \"" << pair.second->getName() << "\", "
            << "\"type\": " << static_cast<int>(pair.second->getType()) << ", "
            << "\"description\": \"" << pair.second->getDescription() << "\""
            << "}";
        first = false;
    }
    oss << "\n  ]\n}";
    return oss.str();
}

std::string MetricsCollector::generateMetricKey(
    const std::string& name,
    const std::map<std::string, std::string>& labels) const {
    std::ostringstream oss;
    oss << name;
    for (const auto& pair : labels) {
        oss << "|" << pair.first << "=" << pair.second;
    }
    return oss.str();
}

Metrics& Metrics::getInstance() {
    static Metrics instance;
    return instance;
}

void Metrics::initialize() {
    initializeDefaultMetrics();
    LOG_INFO("Metrics system initialized");
}

void Metrics::recordRpcRequest(const std::string& service, const std::string& method, double duration_ms) {
    if (!rpc_request_counter_ || !rpc_duration_histogram_) {
        initializeDefaultMetrics();
    }

    rpc_request_counter_->increment();
    rpc_duration_histogram_->observe(duration_ms);

    MetricsCollector::getInstance()
        .createCounter("rpc_requests_total", "Total number of RPC requests",
                       {{"service", service}, {"method", method}})
        ->increment();
    MetricsCollector::getInstance()
        .createHistogram("rpc_duration_ms", "RPC request duration in milliseconds",
                         defaultHistogramBuckets(),
                         {{"service", service}, {"method", method}})
        ->observe(duration_ms);
}

void Metrics::recordRpcResponse(const std::string& service, const std::string& method, int status_code) {
    MetricsCollector::getInstance()
        .createCounter("rpc_responses_total", "Total number of RPC responses",
                       {{"service", service}, {"method", method}, {"status", std::to_string(status_code)}})
        ->increment();
}

void Metrics::recordRpcError(const std::string& service, const std::string& method, const std::string& error_type) {
    if (!rpc_error_counter_) {
        initializeDefaultMetrics();
    }

    rpc_error_counter_->increment();
    MetricsCollector::getInstance()
        .createCounter("rpc_errors_total", "Total number of RPC errors",
                       {{"service", service}, {"method", method}, {"error_type", error_type}})
        ->increment();
}

void Metrics::recordConnection(const std::string& service, bool success) {
    if (!active_connections_gauge_) {
        initializeDefaultMetrics();
    }

    if (success) {
        active_connections_gauge_->increment();
        MetricsCollector::getInstance()
            .createGauge("active_connections", "Number of active connections",
                         {{"service", service}})
            ->increment();
    }
}

void Metrics::recordDisconnection(const std::string& service) {
    if (!active_connections_gauge_) {
        initializeDefaultMetrics();
    }

    active_connections_gauge_->decrement();
    MetricsCollector::getInstance()
        .createGauge("active_connections", "Number of active connections",
                     {{"service", service}})
        ->decrement();
}

void Metrics::recordMessageSent(const std::string& message_type, size_t size) {
    if (!message_counter_) {
        initializeDefaultMetrics();
    }

    message_counter_->increment();
    MetricsCollector::getInstance()
        .createCounter("messages_sent_total", "Total number of messages sent",
                       {{"message_type", message_type}})
        ->increment();
    MetricsCollector::getInstance()
        .createSummary("message_size_bytes", "Observed message sizes",
                       defaultSummaryQuantiles(),
                       {{"direction", "sent"}, {"message_type", message_type}})
        ->observe(static_cast<double>(size));
}

void Metrics::recordMessageReceived(const std::string& message_type, size_t size) {
    if (!message_counter_) {
        initializeDefaultMetrics();
    }

    message_counter_->increment();
    MetricsCollector::getInstance()
        .createCounter("messages_received_total", "Total number of messages received",
                       {{"message_type", message_type}})
        ->increment();
    MetricsCollector::getInstance()
        .createSummary("message_size_bytes", "Observed message sizes",
                       defaultSummaryQuantiles(),
                       {{"direction", "received"}, {"message_type", message_type}})
        ->observe(static_cast<double>(size));
}

void Metrics::recordMemoryUsage(size_t bytes) {
    if (!memory_usage_gauge_) {
        initializeDefaultMetrics();
    }
    memory_usage_gauge_->set(static_cast<double>(bytes));
}

void Metrics::recordCpuUsage(double percentage) {
    if (!cpu_usage_gauge_) {
        initializeDefaultMetrics();
    }
    cpu_usage_gauge_->set(percentage);
}

void Metrics::recordCircuitBreakerState(const std::string& service, int state) {
    if (!circuit_breaker_state_gauge_) {
        circuit_breaker_state_gauge_ = MetricsCollector::getInstance().createGauge(
            "circuit_breaker_state", "Current circuit breaker state");
    }
    circuit_breaker_state_gauge_->set(static_cast<double>(state));
    MetricsCollector::getInstance()
        .createGauge("circuit_breaker_state", "Current circuit breaker state",
                     {{"service", service}})
        ->set(static_cast<double>(state));
}

std::shared_ptr<CounterMetric> Metrics::getRpcRequestCounter() {
    return rpc_request_counter_;
}

std::shared_ptr<HistogramMetric> Metrics::getRpcDurationHistogram() {
    return rpc_duration_histogram_;
}

std::shared_ptr<CounterMetric> Metrics::getRpcErrorCounter() {
    return rpc_error_counter_;
}

std::shared_ptr<GaugeMetric> Metrics::getActiveConnectionsGauge() {
    return active_connections_gauge_;
}

std::shared_ptr<CounterMetric> Metrics::getMessageCounter() {
    return message_counter_;
}

std::shared_ptr<GaugeMetric> Metrics::getMemoryUsageGauge() {
    return memory_usage_gauge_;
}

std::shared_ptr<GaugeMetric> Metrics::getCpuUsageGauge() {
    return cpu_usage_gauge_;
}

std::string Metrics::exportPrometheus() const {
    return MetricsCollector::getInstance().exportPrometheus();
}

std::string Metrics::exportJson() const {
    return MetricsCollector::getInstance().exportJson();
}

void Metrics::initializeDefaultMetrics() {
    rpc_request_counter_ = MetricsCollector::getInstance().createCounter(
        "rpc_requests_total", "Total number of RPC requests");
    rpc_duration_histogram_ = MetricsCollector::getInstance().createHistogram(
        "rpc_duration_ms", "RPC request duration in milliseconds",
        defaultHistogramBuckets());
    rpc_error_counter_ = MetricsCollector::getInstance().createCounter(
        "rpc_errors_total", "Total number of RPC errors");
    active_connections_gauge_ = MetricsCollector::getInstance().createGauge(
        "active_connections", "Number of active connections");
    message_counter_ = MetricsCollector::getInstance().createCounter(
        "messages_total", "Total number of messages");
    memory_usage_gauge_ = MetricsCollector::getInstance().createGauge(
        "memory_usage_bytes", "Memory usage in bytes");
    cpu_usage_gauge_ = MetricsCollector::getInstance().createGauge(
        "cpu_usage_percent", "CPU usage percentage");
    circuit_breaker_state_gauge_ = MetricsCollector::getInstance().createGauge(
        "circuit_breaker_state", "Current circuit breaker state");
}

}  // namespace common
}  // namespace agent_rpc
