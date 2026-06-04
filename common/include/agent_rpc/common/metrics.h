#pragma once

#include "types.h"

#include <atomic>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace agent_rpc {
namespace common {

enum class MetricType {
    COUNTER,
    GAUGE,
    HISTOGRAM,
    SUMMARY
};

struct MetricValue {
    double value;
    std::chrono::system_clock::time_point timestamp;
    std::map<std::string, std::string> labels;
};

struct HistogramBucket {
    double upper_bound = 0.0;
    std::atomic<uint64_t> count{0};

    HistogramBucket() = default;
    explicit HistogramBucket(double bound) : upper_bound(bound), count(0) {}
    HistogramBucket(const HistogramBucket& other)
        : upper_bound(other.upper_bound), count(other.count.load()) {}
    HistogramBucket& operator=(const HistogramBucket& other) {
        if (this != &other) {
            upper_bound = other.upper_bound;
            count.store(other.count.load());
        }
        return *this;
    }
};

class Metric {
public:
    virtual ~Metric() = default;
    virtual MetricType getType() const = 0;
    virtual std::string getName() const = 0;
    virtual std::string getDescription() const = 0;
    virtual std::map<std::string, std::string> getLabels() const = 0;
    virtual std::string toString() const = 0;
};

class CounterMetric : public Metric {
public:
    CounterMetric(const std::string& name,
                  const std::string& description = "",
                  const std::map<std::string, std::string>& labels = {});

    MetricType getType() const override { return MetricType::COUNTER; }
    std::string getName() const override { return name_; }
    std::string getDescription() const override { return description_; }
    std::map<std::string, std::string> getLabels() const override { return labels_; }
    std::string toString() const override;

    void increment(double value = 1.0);
    void set(double value);
    double get() const { return value_.load(); }
    void reset();

private:
    std::string name_;
    std::string description_;
    std::map<std::string, std::string> labels_;
    std::atomic<double> value_{0.0};
};

class GaugeMetric : public Metric {
public:
    GaugeMetric(const std::string& name,
                const std::string& description = "",
                const std::map<std::string, std::string>& labels = {});

    MetricType getType() const override { return MetricType::GAUGE; }
    std::string getName() const override { return name_; }
    std::string getDescription() const override { return description_; }
    std::map<std::string, std::string> getLabels() const override { return labels_; }
    std::string toString() const override;

    void set(double value);
    void increment(double value = 1.0);
    void decrement(double value = 1.0);
    double get() const { return value_.load(); }
    void reset();

private:
    std::string name_;
    std::string description_;
    std::map<std::string, std::string> labels_;
    std::atomic<double> value_{0.0};
};

class HistogramMetric : public Metric {
public:
    HistogramMetric(const std::string& name,
                    const std::string& description = "",
                    const std::vector<double>& buckets = {},
                    const std::map<std::string, std::string>& labels = {});

    MetricType getType() const override { return MetricType::HISTOGRAM; }
    std::string getName() const override { return name_; }
    std::string getDescription() const override { return description_; }
    std::map<std::string, std::string> getLabels() const override { return labels_; }
    std::string toString() const override;

    void observe(double value);
    double getSum() const { return sum_.load(); }
    uint64_t getCount() const { return count_.load(); }
    std::vector<HistogramBucket> getBuckets() const;
    void reset();

private:
    std::string name_;
    std::string description_;
    std::map<std::string, std::string> labels_;
    std::vector<HistogramBucket> buckets_;
    std::atomic<double> sum_{0.0};
    std::atomic<uint64_t> count_{0};
    mutable std::mutex buckets_mutex_;
};

class SummaryMetric : public Metric {
public:
    SummaryMetric(const std::string& name,
                  const std::string& description = "",
                  const std::vector<double>& quantiles = {},
                  const std::map<std::string, std::string>& labels = {});

    MetricType getType() const override { return MetricType::SUMMARY; }
    std::string getName() const override { return name_; }
    std::string getDescription() const override { return description_; }
    std::map<std::string, std::string> getLabels() const override { return labels_; }
    std::string toString() const override;

    void observe(double value);
    double getSum() const { return sum_.load(); }
    uint64_t getCount() const { return count_.load(); }
    std::map<double, double> getQuantiles() const;
    void reset();

private:
    void updateQuantiles();

    std::string name_;
    std::string description_;
    std::map<std::string, std::string> labels_;
    std::vector<double> quantiles_;
    std::vector<double> values_;
    std::map<double, double> quantile_values_;
    std::atomic<double> sum_{0.0};
    std::atomic<uint64_t> count_{0};
    mutable std::mutex values_mutex_;
};

class MetricsCollector {
public:
    static MetricsCollector& getInstance();

    std::shared_ptr<CounterMetric> createCounter(const std::string& name,
                                                 const std::string& description = "",
                                                 const std::map<std::string, std::string>& labels = {});
    std::shared_ptr<GaugeMetric> createGauge(const std::string& name,
                                             const std::string& description = "",
                                             const std::map<std::string, std::string>& labels = {});
    std::shared_ptr<HistogramMetric> createHistogram(const std::string& name,
                                                     const std::string& description = "",
                                                     const std::vector<double>& buckets = {},
                                                     const std::map<std::string, std::string>& labels = {});
    std::shared_ptr<SummaryMetric> createSummary(const std::string& name,
                                                 const std::string& description = "",
                                                 const std::vector<double>& quantiles = {},
                                                 const std::map<std::string, std::string>& labels = {});

    std::shared_ptr<Metric> getMetric(const std::string& name);
    std::vector<std::shared_ptr<Metric>> getAllMetrics();
    void removeMetric(const std::string& name);
    std::string exportPrometheus() const;
    std::string exportJson() const;

private:
    MetricsCollector() = default;

    std::string generateMetricKey(const std::string& name,
                                  const std::map<std::string, std::string>& labels) const;

    mutable std::mutex metrics_mutex_;
    std::map<std::string, std::shared_ptr<Metric>> metrics_;
};

class Metrics {
public:
    static Metrics& getInstance();

    void initialize();

    void recordRpcRequest(const std::string& service, const std::string& method, double duration_ms);
    void recordRpcResponse(const std::string& service, const std::string& method, int status_code);
    void recordRpcError(const std::string& service, const std::string& method, const std::string& error_type);

    void recordConnection(const std::string& service, bool success);
    void recordDisconnection(const std::string& service);

    void recordMessageSent(const std::string& message_type, size_t size);
    void recordMessageReceived(const std::string& message_type, size_t size);

    void recordMemoryUsage(size_t bytes);
    void recordCpuUsage(double percentage);
    void recordCircuitBreakerState(const std::string& service, int state);

    std::shared_ptr<CounterMetric> getRpcRequestCounter();
    std::shared_ptr<HistogramMetric> getRpcDurationHistogram();
    std::shared_ptr<CounterMetric> getRpcErrorCounter();
    std::shared_ptr<GaugeMetric> getActiveConnectionsGauge();
    std::shared_ptr<CounterMetric> getMessageCounter();
    std::shared_ptr<GaugeMetric> getMemoryUsageGauge();
    std::shared_ptr<GaugeMetric> getCpuUsageGauge();

    std::string exportPrometheus() const;
    std::string exportJson() const;

private:
    Metrics() = default;

    void initializeDefaultMetrics();

    std::shared_ptr<CounterMetric> rpc_request_counter_;
    std::shared_ptr<HistogramMetric> rpc_duration_histogram_;
    std::shared_ptr<CounterMetric> rpc_error_counter_;
    std::shared_ptr<GaugeMetric> active_connections_gauge_;
    std::shared_ptr<CounterMetric> message_counter_;
    std::shared_ptr<GaugeMetric> memory_usage_gauge_;
    std::shared_ptr<GaugeMetric> cpu_usage_gauge_;
    std::shared_ptr<GaugeMetric> circuit_breaker_state_gauge_;
};

}  // namespace common
}  // namespace agent_rpc
