#include <iostream>
#include <fstream>
#include <filesystem>
#include <regex>
#include <optional>
#include "json.hpp"
#include "util.h"



namespace fs = std::filesystem;
using json = nlohmann::json;

const fs::path HOME_DIR = std::getenv("HOME") != nullptr
    ? std::getenv("HOME")
    : std::getenv("USERPROFILE");  // Windows 

const fs::path BASE_DIR = getExecutableDir();

const fs::path INPUT_FOLDER = BASE_DIR / ".." /  "Evaluation" / "input";
#ifdef _WIN32
const fs::path SETTING_FILE = BASE_DIR /  "setting_windows.json";
#else
const fs::path SETTING_FILE = BASE_DIR /  "setting.json";
#endif



// Load settings from JSON file
json loadSettings() {
    std::ifstream file(SETTING_FILE);
    if (!file.is_open()) {
       throw std::runtime_error(std::string("Could not open ") + SETTING_FILE.string());
    }
    json settings;
    file >> settings;
    return settings;
}

// Find setting that matches a filename
std::optional<json> findSetting(const std::string& filename, const json& settings) {
    for (const auto& entry : settings) {
        std::regex pattern(entry["pattern"].get<std::string>());
        if (std::regex_match(filename, pattern)) {
            return std::make_optional(entry);

        }
    }
    return std::nullopt;
}

void organizeKif() {
    json settings = loadSettings();

    for (const auto& entry : fs::directory_iterator(INPUT_FOLDER)) {
        if (entry.is_directory()) continue;

        std::string filename = entry.path().filename().string();
        std::string full_path = entry.path().string();

        auto matched_setting = findSetting(filename, settings);
        if (!matched_setting) {
            std::cerr << "Error: setting for player not found in setting.json\n";
            continue;
        }

        std::string pattern = (*matched_setting)["pattern"];
        std::string output_path = (*matched_setting)["output_path"];

        std::smatch match;
        if (std::regex_match(filename, match, std::regex(pattern))) {
            std::string date_str = match[1];
            fs::path target_folder = fs::path(output_path) / date_str;

            if (!fs::exists(target_folder)) {
                fs::create_directories(target_folder);
            }

            fs::path output_file_path = target_folder / filename;
            fs::rename(full_path, output_file_path);
        } else {
            std::cout << "Pattern did not match filename: " << filename << "\n";
            std::cout << "Using pattern: " << pattern << "\n";
        }
    }
}

int main() {
    try {
        organizeKif();
    } catch (const std::exception& e) {
        std::cerr << "Fatal error: " << e.what() << "\n";
        return 1;
    }
    return 0;
}
