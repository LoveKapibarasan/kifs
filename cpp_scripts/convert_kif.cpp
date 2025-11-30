#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <filesystem>
#include <windows.h>

namespace fs = std::filesystem;

// エンコーディング検出
enum class Encoding {
    ASCII,
    UTF8,
    UTF8_BOM,
    SHIFT_JIS,
    UNKNOWN
};

Encoding detectEncoding(const std::vector<uint8_t>& bytes) {
    if (bytes.size() < 2) return Encoding::ASCII;

    // BOMチェック
    if (bytes.size() >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF) {
        return Encoding::UTF8_BOM;
    }

    bool isAscii = true;
    bool isUtf8 = true;
    bool hasSjis = false;

    size_t limit = std::min(bytes.size(), size_t(10000));

    for (size_t i = 0; i < limit; i++) {
        uint8_t b = bytes[i];

        if (b > 127) {
            isAscii = false;

            // UTF-8マルチバイトチェック
            if (b >= 0xC0 && b <= 0xDF) {
                if (i + 1 < bytes.size() && bytes[i + 1] >= 0x80 && bytes[i + 1] <= 0xBF) {
                    i++;
                } else {
                    isUtf8 = false;
                }
            }
            else if (b >= 0xE0 && b <= 0xEF) {
                if (i + 2 < bytes.size() &&
                    bytes[i + 1] >= 0x80 && bytes[i + 1] <= 0xBF &&
                    bytes[i + 2] >= 0x80 && bytes[i + 2] <= 0xBF) {
                    i += 2;
                } else {
                    isUtf8 = false;
                }
            }
            // SHIFT_JIS範囲チェック
            else if ((b >= 0x81 && b <= 0x9F) || (b >= 0xE0 && b <= 0xFC)) {
                hasSjis = true;
                isUtf8 = false;
            }
        }
    }

    if (isAscii) return Encoding::ASCII;
    if (isUtf8) return Encoding::UTF8;
    if (hasSjis) return Encoding::SHIFT_JIS;

    return Encoding::UNKNOWN;
}

std::string encodingToString(Encoding enc) {
    switch (enc) {
        case Encoding::ASCII: return "ASCII";
        case Encoding::UTF8: return "UTF-8";
        case Encoding::UTF8_BOM: return "UTF-8 BOM";
        case Encoding::SHIFT_JIS: return "SHIFT_JIS";
        case Encoding::UNKNOWN: return "UNKNOWN";
    }
    return "UNKNOWN";
}

// SHIFT_JIS → UTF-8 変換
std::string shiftJisToUtf8(const std::vector<uint8_t>& sjisBytes) {
    if (sjisBytes.empty()) return "";

    // SHIFT_JIS → UTF-16
    int wideSize = MultiByteToWideChar(932, 0,
        reinterpret_cast<const char*>(sjisBytes.data()),
        sjisBytes.size(), nullptr, 0);

    if (wideSize == 0) {
        std::cerr << "Error: MultiByteToWideChar failed" << std::endl;
        return "";
    }

    std::wstring wideStr(wideSize, 0);
    MultiByteToWideChar(932, 0,
        reinterpret_cast<const char*>(sjisBytes.data()),
        sjisBytes.size(), &wideStr[0], wideSize);

    // UTF-16 → UTF-8
    int utf8Size = WideCharToMultiByte(CP_UTF8, 0,
        wideStr.c_str(), wideStr.size(),
        nullptr, 0, nullptr, nullptr);

    if (utf8Size == 0) {
        std::cerr << "Error: WideCharToMultiByte failed" << std::endl;
        return "";
    }

    std::string utf8Str(utf8Size, 0);
    WideCharToMultiByte(CP_UTF8, 0,
        wideStr.c_str(), wideStr.size(),
        &utf8Str[0], utf8Size, nullptr, nullptr);

    return utf8Str;
}

// ファイル読み込み
std::vector<uint8_t> readFile(const fs::path& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        throw std::runtime_error("Cannot open file: " + path.string());
    }

    return std::vector<uint8_t>(
        std::istreambuf_iterator<char>(file),
        std::istreambuf_iterator<char>()
    );
}

// ファイル書き込み (UTF-8 BOMなし)
void writeUtf8File(const fs::path& path, const std::string& content) {
    std::ofstream file(path, std::ios::binary);
    if (!file) {
        throw std::runtime_error("Cannot write file: " + path.string());
    }
    file.write(content.c_str(), content.size());
}

// メイン処理
void convertFile(const fs::path& filePath) {
    try {
        // ファイル読み込み
        auto bytes = readFile(filePath);

        // エンコーディング検出
        Encoding enc = detectEncoding(bytes);
        std::cout << "[" << filePath.filename().string() << "] Detected: "
                  << encodingToString(enc) << std::endl;

        std::string utf8Content;
        bool needsConversion = false;

        if (enc == Encoding::SHIFT_JIS || enc == Encoding::UNKNOWN) {
            // SHIFT_JIS → UTF-8
            utf8Content = shiftJisToUtf8(bytes);

            // サンプル表示
            std::string sample = utf8Content.substr(0, std::min(size_t(100), utf8Content.size()));
            std::cout << "  Sample: " << sample << std::endl;

            needsConversion = true;
        }
        else if (enc == Encoding::UTF8_BOM) {
            // BOM削除
            utf8Content = std::string(bytes.begin() + 3, bytes.end());
            needsConversion = true;
            std::cout << "  → Removing BOM..." << std::endl;
        }
        else if (enc == Encoding::UTF8) {
            std::cout << "  → Already UTF-8, skipping" << std::endl;
            return;
        }
        else if (enc == Encoding::ASCII) {
            std::cout << "  → ASCII (no conversion needed)" << std::endl;
            return;
        }

        if (needsConversion) {
            // 一時ファイルに書き込み
            fs::path tmpPath = filePath;
            tmpPath += ".tmp";

            writeUtf8File(tmpPath, utf8Content);

            // 元ファイルを置き換え
            fs::remove(filePath);
            fs::rename(tmpPath, filePath);

            std::cout << "  ✓ Converted to UTF-8" << std::endl;
        }

    } catch (const std::exception& e) {
        std::cerr << "  ✗ Error: " << e.what() << std::endl;
    }
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <directory>" << std::endl;
        return 1;
    }

    fs::path dir(argv[1]);

    if (!fs::exists(dir) || !fs::is_directory(dir)) {
        std::cerr << "Error: Invalid directory: " << dir << std::endl;
        return 1;
    }

    // .kifファイルを検索
    for (const auto& entry : fs::recursive_directory_iterator(dir)) {
        if (entry.is_regular_file() && entry.path().extension() == ".kif") {
            convertFile(entry.path());
        }
    }

    std::cout << "\nConversion complete!" << std::endl;
    return 0;
}