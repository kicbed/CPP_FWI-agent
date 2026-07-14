/**
 * @file vector_store.h
 * @brief VectorStore - simple file-based vector persistence
 *
 * Stores embeddings to JSON files for fast startup.
 * Avoids recomputing vectors on every restart.
 *
 * Storage locations:
 * - resources/embeddings/agent_cards.json   - AgentCard vectors
 * - resources/embeddings/tools.json         - Tool description vectors
 * - resources/embeddings/knowledge.json     - Knowledge base vectors
 */

#pragma once

#include <string>
#include <vector>
#include <map>
#include <fstream>
#include <filesystem>
#include <mutex>
#include <system_error>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace agent_rpc {
namespace orchestrator {

/**
 * @brief Simple file-based vector store
 *
 * Stores embeddings as JSON files for persistence.
 * Loads on startup, saves when vectors change.
 */
class VectorStore {
public:
    explicit VectorStore(const std::string& store_dir = "resources/embeddings")
        : store_dir_(store_dir) {}

    /**
     * @brief Load vectors from file
     * @param name Store name (e.g., "agent_cards", "tools", "knowledge")
     * @return true if loaded successfully
     */
    bool load(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex_);

        std::string path = store_dir_ + "/" + name + ".json";
        std::ifstream file(path);
        if (!file.is_open()) {
            return false;  // File doesn't exist
        }

        try {
            json data;
            file >> data;

            auto& store = stores_[name];
            store.clear();

            for (auto& [key, value] : data.items()) {
                store[key] = value.get<std::vector<float>>();
            }

            return true;
        } catch (const std::exception& e) {
            return false;
        }
    }

    /**
     * @brief Save vectors to file
     * @param name Store name
     * @return true if saved successfully
     */
    bool save(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto it = stores_.find(name);
        if (it == stores_.end()) {
            return false;
        }

        // Create directory if not exists
        std::error_code directory_error;
        std::filesystem::create_directories(store_dir_, directory_error);
        if (directory_error) return false;

        std::string path = store_dir_ + "/" + name + ".json";
        std::ofstream file(path);
        if (!file.is_open()) {
            return false;
        }

        json data;
        for (const auto& [key, vec] : it->second) {
            data[key] = vec;
        }

        file << data.dump(2);
        return true;
    }

    /**
     * @brief Get vector by key
     * @param name Store name
     * @param key Vector key
     * @return Vector if exists, empty vector otherwise
     */
    std::vector<float> get(const std::string& name, const std::string& key) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto store_it = stores_.find(name);
        if (store_it == stores_.end()) {
            return {};
        }

        auto vec_it = store_it->second.find(key);
        if (vec_it == store_it->second.end()) {
            return {};
        }

        return vec_it->second;
    }

    /**
     * @brief Put vector
     * @param name Store name
     * @param key Vector key
     * @param embedding Vector data
     */
    void put(const std::string& name, const std::string& key,
             const std::vector<float>& embedding) {
        std::lock_guard<std::mutex> lock(mutex_);
        stores_[name][key] = embedding;
    }

    /**
     * @brief Remove vector
     * @param name Store name
     * @param key Vector key
     */
    void remove(const std::string& name, const std::string& key) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto store_it = stores_.find(name);
        if (store_it != stores_.end()) {
            store_it->second.erase(key);
        }
    }

    /**
     * @brief Get all keys in a store
     * @param name Store name
     * @return List of keys
     */
    std::vector<std::string> keys(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex_);

        std::vector<std::string> result;
        auto store_it = stores_.find(name);
        if (store_it != stores_.end()) {
            for (const auto& [key, _] : store_it->second) {
                result.push_back(key);
            }
        }
        return result;
    }

    /**
     * @brief Check if key exists
     */
    bool contains(const std::string& name, const std::string& key) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto store_it = stores_.find(name);
        if (store_it == stores_.end()) {
            return false;
        }
        return store_it->second.find(key) != store_it->second.end();
    }

    /**
     * @brief Get store size
     */
    size_t size(const std::string& name) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto store_it = stores_.find(name);
        if (store_it == stores_.end()) {
            return 0;
        }
        return store_it->second.size();
    }

private:
    std::string store_dir_;
    std::map<std::string, std::map<std::string, std::vector<float>>> stores_;
    std::mutex mutex_;
};

} // namespace orchestrator
} // namespace agent_rpc
