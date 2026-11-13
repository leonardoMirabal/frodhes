// mpc_resources.h - find the plugin's own folder and load files from its Content/ folder.
//
// SPDX-License-Identifier: MIT
//
// Included automatically by mpc_plugin.h.
//
//   <Manufacturer> - VST - <Name>/
//   |-- plugin.json
//   |-- VST/<slug>-arm64.so      <- the code that is running
//   |-- Plugin Skins/
//   `-- Content/                 <- samples, wavetables, impulse responses, ... (your files)
//
//   mpc::pluginFolder()            "/.../<Manufacturer> - VST - <Name>"   (no trailing slash)
//   mpc::contentFolder()           pluginFolder() + "/Content"
//   mpc::contentPath("Samples/kick.wav")   full path inside Content/
//   mpc::readFile(path, bytes)     whole file into a vector; false if it cannot be read
//   mpc::readTextFile(path, text)
//
// How it works: the library asks the OS where its own code was loaded from (/proc/self/maps on
// Linux, dladdr on macOS, GetModuleHandleEx on Windows), then walks up from that file to the first
// folder containing plugin.json (inside a macOS .vst bundle: Contents/Resources). If none is found, a parent folder named "VST" is stepped out of,
// else the library's own folder is used.
//
// Override for testing: set the environment variable MPC_PLUGIN_FOLDER to a folder. build.py also
// copies Content/ next to the native test library, which is found automatically.
//
// REALTIME: these functions allocate and do file I/O. Call them from the constructor, prepare(),
// loadExtraState() or a worker you own, NEVER from process(). Load everything into memory up front
// (a std::vector<float> filled in prepare()) and read from that in process().
// Paths always use '/' (works on Windows too). Do not assume a file exists: handle failure by
// falling back to a built-in sound (e.g. a generated waveform) so the plugin still works.

#ifndef MPC_RESOURCES_H
#define MPC_RESOURCES_H

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#if defined(_WIN32)
  #ifndef WIN32_LEAN_AND_MEAN
    #define WIN32_LEAN_AND_MEAN
  #endif
  #include <windows.h>
#else
  #include <dlfcn.h>
  #include <sys/stat.h>
  #include <unistd.h>
#endif

namespace mpc {
namespace detail {

inline void resourceAnchor() {}   // its address lies inside this library

inline std::string normaliseSlashes(std::string s)
{
    for (char& c : s) if (c == '\\') c = '/';
    while (s.size() > 1 && s.back() == '/') s.pop_back();
    return s;
}

inline std::string parentOf(const std::string& p)
{
    size_t i = p.find_last_of('/');
    if (i == std::string::npos) return std::string();
    return i == 0 ? std::string("/") : p.substr(0, i);
}

inline std::string baseNameOf(const std::string& p)
{
    size_t i = p.find_last_of('/');
    return i == std::string::npos ? p : p.substr(i + 1);
}

inline bool fileExists(const std::string& p)
{
    std::FILE* f = std::fopen(p.c_str(), "rb");
    if (f == nullptr) return false;
    std::fclose(f);
    return true;
}

// Full path of the shared library this code lives in, or "" if the OS cannot say.
inline std::string libraryPath()
{
#if defined(_WIN32)
    HMODULE mod = nullptr;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCSTR>(&resourceAnchor), &mod))
        return std::string();
    char buf[4096];
    DWORD n = GetModuleFileNameA(mod, buf, (DWORD)sizeof(buf));
    return n > 0 && n < sizeof(buf) ? normaliseSlashes(std::string(buf, n)) : std::string();
#else
    std::string result;
  #if defined(__linux__)
    // dladdr needs -ldl on older C libraries; /proc/self/maps needs nothing.
    const uintptr_t addr = reinterpret_cast<uintptr_t>(&resourceAnchor);
    if (std::FILE* f = std::fopen("/proc/self/maps", "r"))
    {
        char line[4608];
        while (std::fgets(line, sizeof(line), f) != nullptr)
        {
            unsigned long long lo = 0, hi = 0;
            char perms[8] = {0};
            int pathStart = 0;
            if (std::sscanf(line, "%llx-%llx %7s %*s %*s %*s %n", &lo, &hi, perms, &pathStart) < 3 || pathStart <= 0) continue;
            if (addr < lo || addr >= hi) continue;
            std::string p(line + pathStart);
            while (!p.empty() && (p.back() == '\n' || p.back() == '\r')) p.pop_back();
            const size_t deleted = p.rfind(" (deleted)");
            if (deleted != std::string::npos && deleted + 10 == p.size()) p.resize(deleted);
            result = p;
            break;
        }
        std::fclose(f);
    }
    if (!result.empty()) return result;
  #endif
    Dl_info info;
    if (dladdr(reinterpret_cast<void*>(&resourceAnchor), &info) != 0 && info.dli_fname != nullptr)
    {
        std::string p = info.dli_fname;
        if (!p.empty() && p[0] != '/')   // relative: resolve against the working directory
        {
            char cwd[4096];
            if (getcwd(cwd, sizeof(cwd)) != nullptr) p = std::string(cwd) + "/" + p;
        }
        return p;
    }
    return std::string();
#endif
}

inline std::string computePluginFolder()
{
    if (const char* env = std::getenv("MPC_PLUGIN_FOLDER"))
        if (env[0] != '\0') return normaliseSlashes(env);

    const std::string lib = normaliseSlashes(libraryPath());
    if (lib.empty()) return std::string();

    std::string dir = parentOf(lib);
    // macOS desktop bundle: Name.vst/Contents/MacOS/<binary>; files live in Contents/Resources.
    if (baseNameOf(dir) == "MacOS" && baseNameOf(parentOf(dir)) == "Contents")
        return parentOf(dir) + "/Resources";
    std::string d = dir;
    for (int level = 0; level < 4 && !d.empty(); ++level)   // plugin.json marks the plugin folder
    {
        if (fileExists(d + "/plugin.json")) return d;
        const std::string next = parentOf(d);
        if (next == d) break;
        d = next;
    }
    if (baseNameOf(dir) == "VST") return parentOf(dir);
    return dir;
}

} // namespace detail

// The plugin's own folder ("<Manufacturer> - VST - <Name>"), or "" if it cannot be found.
inline const std::string& pluginFolder()
{
    static const std::string folder = detail::computePluginFolder();
    return folder;
}

inline std::string contentFolder()
{
    const std::string& f = pluginFolder();
    return f.empty() ? std::string("Content") : f + "/Content";
}

// Full path of a file inside Content/. `relative` uses '/' separators, e.g. "Samples/kick.wav".
inline std::string contentPath(const std::string& relative)
{
    std::string r = detail::normaliseSlashes(relative);
    while (!r.empty() && r[0] == '/') r.erase(0, 1);
    return contentFolder() + "/" + r;
}

// Read a whole file. Not realtime-safe.
inline bool readFile(const std::string& path, std::vector<uint8_t>& out)
{
    out.clear();
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) return false;
    uint8_t buf[16384];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) out.insert(out.end(), buf, buf + n);
    const bool ok = std::ferror(f) == 0;
    std::fclose(f);
    if (!ok) out.clear();
    return ok;
}

inline bool readTextFile(const std::string& path, std::string& out)
{
    std::vector<uint8_t> bytes;
    if (!readFile(path, bytes)) { out.clear(); return false; }
    out.assign(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    return true;
}

} // namespace mpc

#endif // MPC_RESOURCES_H
