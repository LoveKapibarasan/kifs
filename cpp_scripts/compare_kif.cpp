#include <iostream>
#include <filesystem>
#include <unordered_set>
#include <string>
#include <cstdlib>

namespace fs = std::filesystem;

// Recursively collect all file names in a folder
void collectFiles(const fs::path& root, std::unordered_set<std::string>& files) {
    for (const auto& entry : fs::recursive_directory_iterator(root)) {
        if (fs::is_regular_file(entry.path())) {
            files.insert(entry.path().filename().string());
        }
    }
}
// Permanently remove file using filesystem (safer than system())
void removeFile(const fs::path& filePath) {
    try {
        fs::remove(filePath);
        std::cout << "Removed: " << filePath << std::endl;
    } catch (const fs::filesystem_error& e) {
        std::cerr << "Error removing " << filePath << ": " << e.what() << std::endl;
    }
}

int main(int argc, char* argv[]) {
    if (argc != 3) {
        std::cerr << "Usage: compare_kif <folder_a> <folder_b>\n"
                  << " From folder_b, duplicated files will be deleted.\n";
        return 1;
    }
    fs::path folderA = fs::absolute(argv[1]);
    fs::path folderB = fs::absolute(argv[2]);
    std::cout << "Comparing " << folderA << " with " << folderB << "\n";
    if (!fs::is_directory(folderA) || !fs::is_directory(folderB)) {
        std::cerr << "Both arguments must be folders.\n";
        return 1;
    }

    std::unordered_set<std::string> filesA;
    collectFiles(folderA, filesA);

    int removed = 0;
    for (const auto& entry : fs::recursive_directory_iterator(folderB)) {
        if (fs::is_regular_file(entry.path())) {
            std::string fname = entry.path().filename().string();
            if (filesA.count(fname)) {
                std::cout << "Removing: " << entry.path() << "\n";
                removeFile(entry.path());
                ++removed;
            }
        }
    }
    std::cout << "Total files removed: " << removed << "\n";
    return 0;
}
