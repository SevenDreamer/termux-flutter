#!/usr/bin/env python3

import os
import sys
import git
import fire
import yaml
import utils
import shutil
import tomllib
import subprocess
from loguru import logger
from pathlib import Path
from sysroot import Sysroot
from package import Package


class GitProgress(git.RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=''):
        logger.trace(f"cloning {cur_count}/{max_count} {message}")


@utils.record
class Build:
    @utils.recordm
    def __init__(self, conf='build.toml'):
        path = Path(__file__).parent
        conf = path/conf

        with open(conf, 'rb') as f:
            cfg = tomllib.load(f)

        ndk = cfg['ndk'].get('path') or os.environ.get('ANDROID_NDK')
        api = cfg['ndk'].get('api')
        tag = cfg['flutter'].get('tag')
        repo = cfg['flutter'].get('repo')
        root = cfg['flutter'].get('path')
        arch = cfg['build'].get('arch')
        mode = cfg['build'].get('runtime')
        gclient = cfg['build'].get('gclient')
        sysroot = cfg['sysroot']
        syspath = sysroot.pop('path')
        package = cfg['package'].get('conf')
        release = cfg['package'].get('path')
        patches = cfg.get('patch')

        if not ndk:
            raise ValueError('neither ndk path nor ANDROID_NDK is set')
        if not tag:
            raise ValueError('require flutter tag')

        # TODO: check parameters
        self.tag = tag
        self.api = api or 26
        self.conf = conf
        # TODO: detect host
        self.host = 'linux-x86_64'
        self.repo = repo or 'https://github.com/flutter/flutter'
        self.arch = arch or 'arm64'
        self.mode = mode or 'debug'
        self.sysroot = Sysroot(path=path/syspath, **sysroot)
        self.root = path/root
        self.gclient = path/gclient
        self.release = path/release
        self.toolchain = Path(ndk, f'toolchains/llvm/prebuilt/{self.host}')

        if not self.release.parent.is_dir():
            raise ValueError(f'bad release path: "{release}"')

        with open(path/package, 'rb') as f:
            self.package = yaml.safe_load(f)

        if isinstance(patches, dict):
            self.patches = {}

            def patch(key):
                return lambda: self.patch(**self.patches[key])

            for k, v in patches.items():
                self.patches[k] = {
                    'file': path/v['file'],
                    'path': self.root/v['path']}
                self.__dict__[f'patch_{k}'] = patch(k)

    def config(self):
        info = (f'{k}\t: {v}' for k, v in self.__dict__.items() if k != 'package')
        logger.info('\n'+'\n'.join(info))

    def clone(self, *, url: str = None, tag: str = None, out: str = None):
        url = url or self.repo
        out = out or self.root
        tag = tag or self.tag
        progress = GitProgress()

        if utils.flutter_tag(out) == tag:
            logger.info('flutter exists, skip.')
            return
        elif os.path.isdir(out):
            logger.info(f'moving {out} to {out}.old ...')
            os.rename(out, f'{out}.old')
            return

        try:
            git.Repo.clone_from(
                url=url,
                to_path=out,
                progress=progress,
                branch=tag)
        except git.exc.GitCommandError:
            raise RuntimeError('\n'.join(progress.error_lines))

    def sync(self, *, cfg: str = None, root: str = None):
        cfg = cfg or self.gclient
        src = root or self.root

        shutil.copy(cfg, os.path.join(src, '.gclient'))
        cmd = ['gclient', 'sync', '-DR', '--no-history']
        subprocess.run(cmd, cwd=src, check=True, stdout=True, stderr=True)

    def setup_termux(self, root: str = None):
        """创建 termux 配置文件（在 sync 后调用）"""
        root = root or self.root
        engine_src = root / 'engine/src'
        
        # 创建 build/config/termux/ 目录
        termux_config_dir = engine_src / 'build/config/termux'
        termux_config_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建 termux.gni
        termux_gni = termux_config_dir / 'termux.gni'
        termux_gni.write_text('''declare_args() {
  termux_api_level = 26
  is_termux = false
  is_termux_host = false
}
''')
        
        # 创建 BUILD.gn
        termux_build_gn = termux_config_dir / 'BUILD.gn'
        termux_build_gn.write_text('''import("//build/config/termux/termux.gni")
import("//build/config/sysroot.gni")
import("//build/config/profiler.gni")

config("compiler") {
  if (current_toolchain == "//build/toolchain/termux:${current_cpu}") {
    cflags = [
      "-fno-strict-aliasing",
      "-fstack-protector",
      "--param=ssp-buffer-size=8",
      "-fPIC",
      "-pipe",
      "-fcolor-diagnostics",
      "-ffunction-sections",
      "-funwind-tables",
      "-fno-short-enums",
      "-nostdinc++",
      "-Wno-unknown-warning-option",
    ]
    cflags_cc = ["-fvisibility-inlines-hidden"]
    cflags_objcc = ["-fvisibility-inlines-hidden"]
    ldflags = [
      "-Wl,--fatal-warnings",
      "-fPIC",
      "-Wl,-z,noexecstack",
      "-Wl,-z,now",
      "-Wl,-z,relro",
      "-Wl,--undefined-version",
      "-Wl,--no-undefined",
      "-Wl,--exclude-libs,ALL",
      "-Wl,--icf=all",
      "-Wl,-z,max-page-size=65536",
    ]
    defines = [
      "__TERMUX__",
      "HAVE_SYS_UIO_H"
    ]
    if (!using_sanitizer) {
      ldflags += [ "-Wl,-z,defs" ]
    }
    if (current_cpu == "arm64") {
      cflags += [ "--target=aarch64-linux-android${termux_api_level}" ]
      ldflags += [ "--target=aarch64-linux-android${termux_api_level}" ]
    } else if (current_cpu == "arm") {
      cflags += [ "--target=arm-linux-androideabi${termux_api_level}" ]
      ldflags += [ "--target=arm-linux-androideabi${termux_api_level}" ]
    } else if (current_cpu == "x86") {
      cflags += [ "--target=i686-linux-androideabi${termux_api_level}" ]
      ldflags += [ "--target=i686-linux-androideabi${termux_api_level}" ]
    } else if (current_cpu == "x64") {
      cflags += [ "--target=x86_64-linux-androideabi${termux_api_level}" ]
      ldflags += [ "--target=x86_64-linux-androideabi${termux_api_level}" ]
    }
    asmflags = cflags
  } else {
    configs = ["//build/config/compiler:compiler"]
  }
}

config("runtime_library") {
  if (current_toolchain == "//build/toolchain/termux:${current_cpu}") {
    cflags_cc = ["-nostdinc++"]
    cflags_objcc = [ "-nostdinc++" ]
    defines = [
      "__compiler_offsetof=__builtin_offsetof",
      "nan=__builtin_nan"
    ]
    ldflags = [
      "-stdlib=libstdc++",
      "-Wl,--warn-shared-textrel"
    ]
    lib_dirs = [ "$custom_toolchain/lib/clang/19/lib/linux/" ]
    include_dirs = [
      "//flutter/third_party/libcxx/include",
      "//flutter/third_party/libcxxabi/include",
    ]
  } else {
    configs = ["//build/config/compiler:runtime_library"]
  }
}

config("executable_ldconfig") {
  if (current_toolchain == "//build/toolchain/termux:${current_cpu}") {
    ldflags = [
      "-Bdynamic",
      "-Wl,-z,nocopyreloc",
    ]
  } else {
    configs = ["//build/config/gcc:executable_ldconfig"]
  }
}

config("sdk") {
  cflags = []
  ldflags = [ "-Wl,-rpath=/data/data/com.termux/files/usr/lib" ]
  lib_dirs = [ "$custom_toolchain/lib/clang/19/lib/linux/" ]
  if (defined(target_sysroot) && target_sysroot != "") {
    cflags += [ "--sysroot=" + target_sysroot ]
    ldflags += [ "--sysroot=" + target_sysroot ]
  }
  if (defined(custom_sysroot) && custom_sysroot != "") {
    cflags += [ "-idirafter$custom_sysroot/usr/include" ]
    lib_dirs += [ "$custom_sysroot/usr/lib" ]
  }
}
''')
        
        # 创建 build/toolchain/termux/ 目录
        termux_toolchain_dir = engine_src / 'build/toolchain/termux'
        termux_toolchain_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建 toolchain BUILD.gn
        toolchain_build_gn = termux_toolchain_dir / 'BUILD.gn'
        toolchain_build_gn.write_text('''import("//build/toolchain/gcc_toolchain.gni")
import("//build/config/android/config.gni")
import("//build/config/termux/termux.gni")
import("//build/toolchain/custom/custom.gni")

template("termux_toolchain") {
  gcc_toolchain(target_name) {
    assert(defined(custom_toolchain) && custom_toolchain != "")

    is_clang = true
    toolchain_os = "linux"
    toolchain_cpu = invoker.toolchain_cpu

    prefix = "$custom_toolchain/bin"
    cc = prefix + "/clang"
    cxx = prefix + "/clang++"
    asm = prefix + "/clang"
    ar = prefix + "/llvm-ar"
    ld = prefix + "/clang++"
    readelf = prefix + "/llvm-readelf"
    nm = prefix + "/llvm-nm"
    strip = prefix + "/llvm-strip"
  }
}

termux_toolchain("arm64"){
  toolchain_cpu = "arm64"
}

termux_toolchain("arm"){
  toolchain_cpu = "arm"
}

termux_toolchain("x64"){
  toolchain_cpu = "x64"
}

termux_toolchain("x86"){
  toolchain_cpu = "x86"
}
''')
        
        logger.info(f"✓ Created termux config in {termux_config_dir}")
        logger.info(f"✓ Created termux toolchain in {termux_toolchain_dir}")
        
        # 修改 BUILDCONFIG.gn
        buildconfig_path = engine_src / 'build/config/BUILDCONFIG.gn'
        if buildconfig_path.exists():
            content = buildconfig_path.read_text()
            
            # 检查是否已经修改
            if 'is_termux' not in content:
                # 1. 替换 compiler config
                content = content.replace(
                    '"//build/config/compiler:compiler"',
                    '"//build/config/termux:compiler"'
                )
                # 2. 替换 runtime_library
                content = content.replace(
                    '"//build/config/compiler:runtime_library"',
                    '"//build/config/termux:runtime_library"'
                )
                # 3. 添加 termux.gni import 和 is_termux 判断
                old_linux_check = 'if (is_linux) {\n  _native_compiler_configs += [ "//build/config/linux:sdk" ]'
                new_termux_check = '''import("//build/config/termux/termux.gni")

if (is_termux) {
  _native_compiler_configs += [ "//build/config/termux:sdk" ]
} else if (is_linux) {
  _native_compiler_configs += [ "//build/config/linux:sdk" ]'''
                content = content.replace(old_linux_check, new_termux_check)
                
                # 4. 替换 executable_ldconfig
                content = content.replace(
                    '"//build/config/gcc:executable_ldconfig"',
                    '"//build/config/termux:executable_ldconfig"'
                )
                
                # 5. 添加 is_termux toolchain 处理
                old_custom_check = 'if (custom_toolchain != "") {\n  assert(custom_sysroot != "")\n  assert(custom_target_triple != "")\n  host_toolchain = "//build/toolchain/linux:clang_$host_cpu"'
                new_termux_toolchain = '''if (is_termux) {
  host_toolchain = "//build/toolchain/linux:clang_$host_cpu"
  set_default_toolchain("//build/toolchain/termux:$current_cpu")
} else if (custom_toolchain != "") {
  assert(custom_sysroot != "")
  assert(custom_target_triple != "")
  host_toolchain = "//build/toolchain/linux:clang_$host_cpu"'''
                content = content.replace(old_custom_check, new_termux_toolchain)
                
                buildconfig_path.write_text(content)
                logger.info(f"✓ Patched BUILDCONFIG.gn")
            else:
                logger.info("✓ BUILDCONFIG.gn already patched")
        
        # 修改 sysroot.gni
        sysroot_gni_path = engine_src / 'build/config/sysroot.gni'
        if sysroot_gni_path.exists():
            content = sysroot_gni_path.read_text()
            
            if 'is_termux' not in content:
                old_sysroot = '''if (current_toolchain == default_toolchain && target_sysroot != "") {
  sysroot = target_sysroot
} else if (is_android) {'''
                new_sysroot = '''if (current_toolchain == default_toolchain && target_sysroot != "") {
  sysroot = target_sysroot
} else if (is_termux && custom_sysroot != "") {
  sysroot = custom_sysroot
} else if (is_android) {'''
                content = content.replace(old_sysroot, new_sysroot)
                sysroot_gni_path.write_text(content)
                logger.info(f"✓ Patched sysroot.gni")
            else:
                logger.info("✓ sysroot.gni already patched")
        
        # 修改 flutter/shell/testing/BUILD.gn (swiftshader 链接)
        testing_build_path = engine_src / 'flutter/shell/testing/BUILD.gn'
        if testing_build_path.exists():
            content = testing_build_path.read_text()
            
            if 'is_termux' not in content:
                # 在 swiftshader 依赖后添加 termux 处理
                old_testing = '''      "//flutter/third_party/swiftshader/src/Vulkan:swiftshader_libvulkan_static",
    ]
  }'''
                new_testing = '''      "//flutter/third_party/swiftshader/src/Vulkan:swiftshader_libvulkan_static",
    ]
    if (is_termux) {
      libs += [ "vk_swiftshader" ]
      deps -= [ "//flutter/third_party/swiftshader/src/Vulkan:swiftshader_libvulkan_static" ]
    }
  }'''
                content = content.replace(old_testing, new_testing)
                testing_build_path.write_text(content)
                logger.info(f"✓ Patched testing/BUILD.gn")
            else:
                logger.info("✓ testing/BUILD.gn already patched")
        
        # 修改 flutter/shell/platform/linux/BUILD.gn (跳过 config 依赖)
        linux_shell_path = engine_src / 'flutter/shell/platform/linux/BUILD.gn'
        if linux_shell_path.exists():
            content = linux_shell_path.read_text()
            
            if 'is_termux' not in content:
                # 添加 termux.gni import
                content = content.replace(
                    'import("//flutter/shell/platform/linux/config/config.gni")\n',
                    'import("//flutter/shell/platform/linux/config/config.gni")\nimport("//build/config/termux/termux.gni")\n'
                )
                # 跳过 config:gtk 依赖
                content = content.replace(
                    'configs += [ "//flutter/shell/platform/linux/config:gtk" ]',
                    'if (!is_termux) { configs += [ "//flutter/shell/platform/linux/config:gtk" ] }'
                )
                linux_shell_path.write_text(content)
                logger.info(f"✓ Patched linux/BUILD.gn")
            else:
                logger.info("✓ linux/BUILD.gn already patched")
        
        # 创建 Android Vulkan 扩展 stub 头文件 (swiftshader 需要)
        swiftshader_vulkan_dir = engine_src / 'flutter/third_party/swiftshader/include/vulkan'
        swiftshader_vulkan_dir.mkdir(parents=True, exist_ok=True)
        vk_android_header = swiftshader_vulkan_dir / 'vk_android_native_buffer.h'
        if not vk_android_header.exists():
            vk_android_header.write_text('''// Stub header for Termux build
// Original is part of Android NDK Vulkan extensions

#ifndef VULKAN_VK_ANDROID_NATIVE_BUFFER_H_
#define VULKAN_VK_ANDROID_NATIVE_BUFFER_H_ 1

#include "vulkan.h"

#ifdef __cplusplus
extern "C" {
#endif

// This should be a bitmask type (integer), not a struct
typedef VkFlags VkSwapchainImageUsageFlagsANDROID;

typedef struct VkNativeBufferUsageANDROID {
    VkStructureType sType;
    void* pNext;
    uint32_t androidUsage;
} VkNativeBufferUsageANDROID;

// Forward declare native_handle for compatibility
struct native_handle;
typedef struct native_handle native_handle_t;
// buffer_handle_t is compatible with native_handle_t*
typedef const native_handle_t* buffer_handle_t;

typedef struct VkNativeBufferANDROID {
    VkStructureType sType;
    void* pNext;
    uint32_t allocationSize;
    uint32_t* pStride;
    void* buffer;
    uint32_t offset;
    uint32_t range;
    // Additional fields needed by swiftshader
    buffer_handle_t handle;
    int stride;
    // Direct format/usage fields (accessed by swiftshader VkImage.cpp)
    int format;
    int usage;
} VkNativeBufferANDROID;

// Additional Android Vulkan extension types
typedef struct VkPhysicalDevicePresentationPropertiesANDROID {
    VkStructureType sType;
    void* pNext;
    VkBool32 sharedImage;  // swiftshader uses 'sharedImage', not 'supportsImageSharing'
} VkPhysicalDevicePresentationPropertiesANDROID;

typedef struct VkAndroidHardwareBufferUsageANDROID {
    VkStructureType sType;
    void* pNext;
    VkFlags64 androidHardwareBufferUsage;
} VkAndroidHardwareBufferUsageANDROID;

// Additional Android Hardware Buffer types needed by swiftshader
typedef struct VkAndroidHardwareBufferFormatPropertiesANDROID {
    VkStructureType sType;
    void* pNext;
    uint32_t format;
    uint64_t externalFormat;
    uint64_t formatFeatures;
    uint32_t samplerYcbcrConversionComponents[4];
    uint32_t suggestedYcbcrModel;
    uint32_t suggestedYcbcrRange;
    uint32_t suggestedXChromaOffset;
    uint32_t suggestedYChromaOffset;
} VkAndroidHardwareBufferFormatPropertiesANDROID;

typedef struct VkAndroidHardwareBufferPropertiesANDROID {
    VkStructureType sType;
    void* pNext;
    uint64_t allocationSize;
    uint32_t memoryTypeBits;
} VkAndroidHardwareBufferPropertiesANDROID;

typedef struct VkExternalFormatANDROID {
    VkStructureType sType;
    void* pNext;
    uint64_t externalFormat;
} VkExternalFormatANDROID;

#define VK_STRUCTURE_TYPE_NATIVE_BUFFER_ANDROID 1000000006
#define VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENTATION_PROPERTIES_ANDROID 1000000007
#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_USAGE_ANDROID 1000000008
#define VK_STRUCTURE_TYPE_SWAPCHAIN_IMAGE_CREATE_INFO_ANDROID 1000000009
#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID 1000129001
#define VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_FORMAT_PROPERTIES_ANDROID 1000129002
#define VK_STRUCTURE_TYPE_EXTERNAL_FORMAT_ANDROID 1000129004

// Android Vulkan extension function stubs
typedef VkResult (VKAPI_PTR *PFN_vkGetSwapchainGrallocUsageANDROID)(VkDevice device, VkFormat format, VkImageUsageFlags imageUsage, int* grallocUsage);
typedef VkResult (VKAPI_PTR *PFN_vkGetSwapchainGrallocUsage2ANDROID)(VkDevice device, VkFormat format, VkImageUsageFlags imageUsage, VkSwapchainImageUsageFlagsANDROID swapchainImageUsage, uint64_t* grallocUsage, uint64_t* grallocUsage2);
typedef VkResult (VKAPI_PTR *PFN_vkAcquireImageANDROID)(VkDevice device, VkImage image, int nativeFenceFd, VkSemaphore semaphore, VkFence fence);
typedef VkResult (VKAPI_PTR *PFN_vkQueueSignalReleaseImageANDROID)(VkQueue queue, uint32_t waitSemaphoreCount, const VkSemaphore* pWaitSemaphores, VkImage image, int* pNativeFenceFd);

// Stub implementations (return success)
static inline VkResult vkGetSwapchainGrallocUsageANDROID(VkDevice device, VkFormat format, VkImageUsageFlags imageUsage, int* grallocUsage) { *grallocUsage = 0; return VK_SUCCESS; }
static inline VkResult vkGetSwapchainGrallocUsage2ANDROID(VkDevice device, VkFormat format, VkImageUsageFlags imageUsage, VkSwapchainImageUsageFlagsANDROID swapchainImageUsage, uint64_t* grallocUsage, uint64_t* grallocUsage2) { *grallocUsage = 0; *grallocUsage2 = 0; return VK_SUCCESS; }
static inline VkResult vkAcquireImageANDROID(VkDevice device, VkImage image, int nativeFenceFd, VkSemaphore semaphore, VkFence fence) { return VK_SUCCESS; }
static inline VkResult vkQueueSignalReleaseImageANDROID(VkQueue queue, uint32_t waitSemaphoreCount, const VkSemaphore* pWaitSemaphores, VkImage image, int* pNativeFenceFd) { *pNativeFenceFd = -1; return VK_SUCCESS; }

#ifdef __cplusplus
}
#endif

#endif  // VULKAN_VK_ANDROID_NATIVE_BUFFER_H_
''')
        logger.info(f"✓ Created vulkan/vk_android_native_buffer.h stub")
        
        # 创建 hardware/hwvulkan.h stub
        hardware_dir = engine_src / 'hardware'
        hardware_dir.mkdir(parents=True, exist_ok=True)
        hwvulkan_header = hardware_dir / 'hwvulkan.h'
        hwvulkan_header.write_text('''// Stub header for Termux build
// Original is part of Android NDK hardware headers

#ifndef HARDWARE_HWVULKAN_H_
#define HARDWARE_HWVULKAN_H_

#include <stdint.h>
#include <sys/cdefs.h>

// Include Vulkan core definitions instead of redefining
#include "vulkan/vulkan_core.h"

__BEGIN_DECLS

#define HWVULKAN_HARDWARE_MODULE_ID "vulkan"
#define HARDWARE_MODULE_TAG 'HWCT'
#define HARDWARE_DEVICE_TAG 'HWDT'
#define HARDWARE_HAL_API_VERSION 1
#define HWVULKAN_MODULE_API_VERSION_0_1 1
#define HWVULKAN_DEVICE_API_VERSION_0_1 1
#define HWVULKAN_DEVICE_0 "vulkan0"

typedef struct hw_device_t {
    uint32_t tag;
    uint32_t version;
    struct hw_module_t* module;
    uint32_t reserved[12];
    int (*close)(struct hw_device_t* device);
} hw_device_t;

typedef struct hw_module_t {
    uint32_t tag;
    uint16_t module_api_version;
    uint16_t hal_api_version;
    const char* id;
    const char* name;
    const char* author;
    struct hw_module_methods_t* methods;
    uint32_t reserved[32-7];
} hw_module_t;

typedef struct hw_module_methods_t {
    int (*open)(const struct hw_module_t* module, const char* id, struct hw_device_t** device);
} hw_module_methods_t;

typedef struct hwvulkan_module_t {
    hw_module_t common;
} hwvulkan_module_t;

typedef struct hwvulkan_device_t {
    hw_device_t common;
    // Vulkan function pointers required by swiftshader
    PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
    PFN_vkCreateInstance CreateInstance;
    PFN_vkGetInstanceProcAddr GetInstanceProcAddr;
} hwvulkan_device_t;

__END_DECLS

#endif  // HARDWARE_HWVULKAN_H_
''')
        logger.info(f"✓ Created hardware/hwvulkan.h stub")
        
        # 创建 hardware/gralloc.h stub (swiftshader 需要)
        gralloc_header = hardware_dir / 'gralloc.h'
        gralloc_header.write_text('''// Stub header for Termux build
// Original is part of Android NDK hardware headers

#ifndef HARDWARE_GRALLOC_H_
#define HARDWARE_GRALLOC_H_

#include <stdint.h>
#include <sys/cdefs.h>

__BEGIN_DECLS

#define GRALLOC_HARDWARE_MODULE_ID "gralloc"
#define GRALLOC_HARDWARE_GPU0 "gpu0"

// Buffer usage flags
#define GRALLOC_USAGE_SW_READ_NEVER 0x00000000
#define GRALLOC_USAGE_SW_READ_RARELY 0x00000002
#define GRALLOC_USAGE_SW_READ_OFTEN 0x00000003
#define GRALLOC_USAGE_SW_READ_MASK 0x0000000F
#define GRALLOC_USAGE_SW_WRITE_NEVER 0x00000000
#define GRALLOC_USAGE_SW_WRITE_RARELY 0x00000020
#define GRALLOC_USAGE_SW_WRITE_OFTEN 0x00000030
#define GRALLOC_USAGE_SW_WRITE_MASK 0x000000F0
#define GRALLOC_USAGE_HW_TEXTURE 0x00000100
#define GRALLOC_USAGE_HW_RENDER 0x00000200
#define GRALLOC_USAGE_HW_2D 0x00000400
#define GRALLOC_USAGE_HW_COMPOSER 0x00000800
#define GRALLOC_USAGE_HW_FB 0x00001000
#define GRALLOC_USAGE_HW_VIDEO_ENCODER 0x00010000
#define GRALLOC_USAGE_HW_CAMERA_WRITE 0x00020000
#define GRALLOC_USAGE_HW_CAMERA_READ 0x00040000
#define GRALLOC_USAGE_HW_CAMERA_MASK 0x00060000
#define GRALLOC_USAGE_HW_MASK 0x00071F00

// Pixel formats
#define HAL_PIXEL_FORMAT_RGBA_8888 1
#define HAL_PIXEL_FORMAT_RGBX_8888 2
#define HAL_PIXEL_FORMAT_RGB_888 3
#define HAL_PIXEL_FORMAT_RGB_565 4
#define HAL_PIXEL_FORMAT_BGRA_8888 5
#define HAL_PIXEL_FORMAT_RGBA_5551 6
#define HAL_PIXEL_FORMAT_RGBA_4444 7

typedef struct android_native_base_t {
    int magic;
    int version;
    void* reserved[4];
    void (*incRef)(struct android_native_base_t* base);
    void (*decRef)(struct android_native_base_t* base);
} android_native_base_t;

typedef struct ANativeWindowBuffer {
    android_native_base_t common;
    int width;
    int height;
    int stride;
    int format;
    int usage;
    void* reserved[2];
    void* handle;
    void* reserved_proc[8];
} ANativeWindowBuffer_t;

// Minimal gralloc module structure
typedef struct gralloc_module_t {
    struct hw_module_t common;
    int (*registerBuffer)(struct gralloc_module_t const* module, void* handle);
    int (*unregisterBuffer)(struct gralloc_module_t const* module, void* handle);
    int (*lock)(struct gralloc_module_t const* module, void* handle, int usage, int l, int t, int w, int h, void** vaddr);
    int (*unlock)(struct gralloc_module_t const* module, void* handle);
    int (*perform)(struct gralloc_module_t const* module, int operation, ...);
    void* reserved_proc[7];
} gralloc_module_t;

typedef struct alloc_device_t {
    struct hw_device_t common;
    int (*alloc)(struct alloc_device_t* dev, int w, int h, int format, int usage, void** handle, int* stride);
    int (*free)(struct alloc_device_t* dev, void* handle);
    void* reserved_proc[7];
} alloc_device_t;

__END_DECLS

#endif  // HARDWARE_GRALLOC_H_
''')
        logger.info(f"✓ Created hardware/gralloc.h stub")
        
        # 创建 vndk/hardware_buffer.h stub
        vndk_dir = engine_src / 'vndk'
        vndk_dir.mkdir(parents=True, exist_ok=True)
        hwbuffer_header = vndk_dir / 'hardware_buffer.h'
        hwbuffer_header.write_text('''// Stub header for Termux build
// Original is part of Android NDK VNDK

#ifndef VNDK_HARDWARE_BUFFER_H_
#define VNDK_HARDWARE_BUFFER_H_

#include <stdint.h>
#include <sys/cdefs.h>

__BEGIN_DECLS

// native_handle_t forward declaration
struct native_handle;
typedef struct native_handle native_handle_t;

typedef const native_handle_t* AHardwareBuffer;

typedef enum {
    AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM = 1,
    AHARDWAREBUFFER_FORMAT_R8G8B8X8_UNORM = 2,
    AHARDWAREBUFFER_FORMAT_R8G8B8_UNORM = 3,
    AHARDWAREBUFFER_FORMAT_R5G6B5_UNORM = 4,
    AHARDWAREBUFFER_FORMAT_R16G16B16A16_FLOAT = 0x16,
    AHARDWAREBUFFER_FORMAT_R10G10B10A2_UNORM = 0x2b,
    AHARDWAREBUFFER_FORMAT_BLOB = 0x21,
} AHardwareBufferFormat;

typedef enum {
    AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE = 1ULL << 0,
    AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT = 1ULL << 2,
    AHARDWAREBUFFER_USAGE_GPU_CUBE_MAP = 1ULL << 7,
    AHARDWAREBUFFER_USAGE_GPU_MIPMAP_COMPLETE = 1ULL << 8,
    AHARDWAREBUFFER_USAGE_GPU_DATA_BUFFER = 1ULL << 24,
    AHARDWAREBUFFER_USAGE_PROTECTED_CONTENT = 1ULL << 14,
    AHARDWAREBUFFER_USAGE_VIDEO_ENCODE = 1ULL << 6,
    AHARDWAREBUFFER_USAGE_VIDEO_DECODE = 1ULL << 13,
} AHardwareBufferUsage;

typedef struct AHardwareBuffer_Desc {
    uint32_t width;
    uint32_t height;
    uint32_t layers;
    uint32_t format;
    uint64_t usage;
    uint32_t stride;
    uint32_t rfu0;
    uint64_t rfu1;
} AHardwareBuffer_Desc;

// Additional enums and structs needed by swiftshader
typedef enum {
    AHARDWAREBUFFER_USAGE_CPU_READ_RARELY = 1ULL << 30,
    AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN = 3ULL << 30,
    AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY = 1ULL << 28,
    AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN = 3ULL << 28,
} AHardwareBufferUsage2;

typedef struct ARect {
    int32_t left;
    int32_t top;
    int32_t right;
    int32_t bottom;
} ARect;

typedef struct AHardwareBuffer_Plane {
    void* data;
    uint32_t pixelStride;
    uint32_t rowStride;
} AHardwareBuffer_Plane;

typedef struct AHardwareBuffer_Planes {
    uint32_t planeCount;
    AHardwareBuffer_Plane planes[4];
} AHardwareBuffer_Planes;

#define AHARDWAREBUFFER_CREATE_FROM_HANDLE_METHOD_CLONE 1

// Function stubs
inline int AHardwareBuffer_describe(AHardwareBuffer* buffer, AHardwareBuffer_Desc* outDesc) {
    (void)buffer; (void)outDesc; return 0;
}

inline void AHardwareBuffer_acquire(AHardwareBuffer* buffer) { (void)buffer; }
inline void AHardwareBuffer_release(AHardwareBuffer* buffer) { (void)buffer; }
inline int AHardwareBuffer_lock(AHardwareBuffer* buffer, uint64_t usage, int32_t fence, const ARect* rect, void** outVirtualAddr) {
    (void)buffer; (void)usage; (void)fence; (void)rect; *outVirtualAddr = nullptr; return 0;
}
inline int AHardwareBuffer_unlock(AHardwareBuffer* buffer, int32_t* fence) {
    (void)buffer; if (fence) *fence = -1; return 0;
}
inline int AHardwareBuffer_lockPlanes(AHardwareBuffer* buffer, uint64_t usage, int32_t fence, const ARect* rect, AHardwareBuffer_Planes* outPlanes) {
    (void)buffer; (void)usage; (void)fence; (void)rect; (void)outPlanes; return -1;
}
inline int AHardwareBuffer_createFromHandle(const AHardwareBuffer_Desc* desc, const native_handle_t* handle, int method, AHardwareBuffer** outBuffer) {
    (void)desc; (void)handle; (void)method; *outBuffer = nullptr; return -1;
}

__END_DECLS

#endif  // VNDK_HARDWARE_BUFFER_H_
''')
        logger.info(f"✓ Created vndk/hardware_buffer.h stub")
        
        # 修复 swiftshader VkDeviceMemory.hpp - 确保 exportAndroidHardwareBuffer 签名正确
        vk_device_memory_hpp = engine_src / 'flutter/third_party/swiftshader/src/Vulkan/VkDeviceMemory.hpp'
        if vk_device_memory_hpp.exists():
            content = vk_device_memory_hpp.read_text()
            import re
            # 检查是否已经有正确的签名
            if 'exportAndroidHardwareBuffer(AHardwareBuffer **pAhb)' not in content:
                # 先尝试替换任何现有的 exportAndroidHardwareBuffer 定义
                pattern = r'virtual\s+VkResult\s+exportAndroidHardwareBuffer\s*\([^)]*\)\s*const[^;]*;'
                replacement = 'virtual VkResult exportAndroidHardwareBuffer(AHardwareBuffer **pAhb) const;'
                new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
                
                if new_content != content:
                    # 替换成功
                    content = new_content
                    logger.info(f"✓ Fixed exportAndroidHardwareBuffer signature in VkDeviceMemory.hpp")
                else:
                    # 替换失败，尝试多种方式添加
                    # 方式1: 在析构函数后添加
                    patterns_to_try = [
                        r'(virtual\s+~DeviceMemory\s*\([^)]*\)\s*;)',
                        r'(virtual\s+~DeviceMemory\s*\([^)]*\)\s*[^;]*;)',
                        r'(~DeviceMemory\s*\([^)]*\))',
                        r'(class\s+DeviceMemory[^{]*\{)',  # 在类定义开头后添加
                    ]
                    added = False
                    for pattern in patterns_to_try:
                        match = re.search(pattern, content)
                        if match:
                            # 找到匹配位置，在后面添加
                            insertion = '''

    // Android Hardware Buffer export function (required by VkDeviceMemoryExternalAndroid)
    virtual VkResult exportAndroidHardwareBuffer(AHardwareBuffer **pAhb) const { return VK_ERROR_OUT_OF_DEVICE_MEMORY; }
'''
                            # 如果是类定义开头，需要在第一个 protected/private/public 后添加
                            if 'class DeviceMemory' in pattern:
                                # 找到第一个 public/protected
                                pub_match = re.search(r'(public\s*:)', content[match.end():])
                                if pub_match:
                                    insert_pos = match.end() + pub_match.end()
                                else:
                                    insert_pos = match.end()
                            else:
                                insert_pos = match.end()
                            
                            content = content[:insert_pos] + insertion + content[insert_pos:]
                            added = True
                            logger.info(f"✓ Added exportAndroidHardwareBuffer to VkDeviceMemory.hpp after pattern: {pattern}")
                            break
                    
                    if not added:
                        # 最后手段：在文件末尾的类定义闭合括号前添加
                        # 找最后一个 }; 
                        last_brace = content.rfind('};')
                        if last_brace > 0:
                            insertion = '''

    // Android Hardware Buffer export function (required by VkDeviceMemoryExternalAndroid)
    virtual VkResult exportAndroidHardwareBuffer(AHardwareBuffer **pAhb) const { return VK_ERROR_OUT_OF_DEVICE_MEMORY; }
'''
                            content = content[:last_brace] + insertion + content[last_brace:]
                            logger.info(f"✓ Added exportAndroidHardwareBuffer to VkDeviceMemory.hpp before closing brace")
                
                # 确保 include 了 vndk/hardware_buffer.h
                if '#include "vndk/hardware_buffer.h"' not in content and '#include <vndk/hardware_buffer.h>' not in content:
                    match = re.search(r'(#include\s+[<"][^>"]+[>"])', content)
                    if match:
                        content = content[:match.end()] + '\n#include "vndk/hardware_buffer.h"' + content[match.end():]
                
                vk_device_memory_hpp.write_text(content)
            else:
                logger.info(f"✓ VkDeviceMemory.hpp already has correct exportAndroidHardwareBuffer signature")
        else:
            logger.warning(f"VkDeviceMemory.hpp not found at {vk_device_memory_hpp}")
        
        # 修复 swiftshader VkDeviceMemoryExternalAndroid.hpp - 删除冲突的前向声明，添加正确的 include
        # 同时删除 override 关键字避免签名不匹配问题
        vk_device_memory_external_hpp = engine_src / 'flutter/third_party/swiftshader/src/Vulkan/VkDeviceMemoryExternalAndroid.hpp'
        if vk_device_memory_external_hpp.exists():
            content = vk_device_memory_external_hpp.read_text()
            import re
            # 删除第一行的 "struct AHardwareBuffer;" 前向声明（与 typedef 冲突）
            content = re.sub(r'^struct\s+AHardwareBuffer;\s*\n', '', content)
            # 添加 include vndk/hardware_buffer.h（如果不存在）
            if '#include "vndk/hardware_buffer.h"' not in content and '#include <vndk/hardware_buffer.h>' not in content:
                # 在第一个 #include 后添加
                match = re.search(r'(#include\s+[<"][^>"]+[>"])', content)
                if match:
                    content = content[:match.end()] + '\n#include "vndk/hardware_buffer.h"' + content[match.end():]
            # 修复 exportAndroidHardwareBuffer - 只删除 override final 组合，保留普通 override
            # 原始: virtual VkResult exportAndroidHardwareBuffer(...) const override final;
            # 修改: virtual VkResult exportAndroidHardwareBuffer(...) const;
            # 注意：不能删除普通 override，否则其他方法会报 -Winconsistent-missing-override 错误
            content = re.sub(r'override\s+final\s*;', ';', content)
            
            # 给缺少 override 的方法添加 override 关键字
            # 这些方法重写了基类的虚函数，但声明时没写 override
            content = re.sub(r'(VkResult\s+allocateBuffer\s*\(\s*\))\s*;', r'\1 override;', content)
            content = re.sub(r'(void\s+freeBuffer\s*\(\s*\))\s*;', r'\1 override;', content)
            content = re.sub(r'(int\s+externalImageRowPitchBytes\s*\([^)]*\)\s*const)\s*;', r'\1 override;', content)
            content = re.sub(r'(VkDeviceSize\s+externalImageMemoryOffset\s*\([^)]*\)\s*const)\s*;', r'\1 override;', content)
            
            vk_device_memory_external_hpp.write_text(content)
            logger.info(f"✓ Fixed VkDeviceMemoryExternalAndroid.hpp - removed conflicting forward declaration, added include, fixed override keywords")
        else:
            logger.warning(f"VkDeviceMemoryExternalAndroid.hpp not found at {vk_device_memory_external_hpp}")
        
        # 添加缺失的 Android Vulkan 扩展结构体定义
        # vk_android_native_buffer.h 已经有这些定义，这里创建一个简单的转发头文件
        vk_android_extensions_h = engine_src / 'flutter/third_party/swiftshader/src/Vulkan/vk_android_extensions.h'
        if not vk_android_extensions_h.exists():
            vk_android_extensions_h.write_text('''// Forward to existing definitions
// Android Vulkan extensions are defined in vk_android_native_buffer.h
#ifndef VK_ANDROID_EXTENSIONS_H_
#define VK_ANDROID_EXTENSIONS_H_

#include "vulkan/vk_android_native_buffer.h"

#endif  // VK_ANDROID_EXTENSIONS_H_
''')
            logger.info(f"✓ Created vk_android_extensions.h")
        
        # 确保 VkPhysicalDevice.hpp include 了 vk_android_extensions.h
        vk_physical_device_hpp = engine_src / 'flutter/third_party/swiftshader/src/Vulkan/VkPhysicalDevice.hpp'
        if vk_physical_device_hpp.exists():
            content = vk_physical_device_hpp.read_text()
            if '#include "vk_android_extensions.h"' not in content:
                # 在第一个 #include 后添加
                match = re.search(r'(#include\s+[<"][^>"]+[>"])', content)
                if match:
                    content = content[:match.end()] + '\n#include "vk_android_extensions.h"' + content[match.end():]
                    vk_physical_device_hpp.write_text(content)
                    logger.info(f"✓ Added #include vk_android_extensions.h to VkPhysicalDevice.hpp")

        # 创建 swiftshader commit.h (git版本信息文件，否则编译失败)
        swiftshader_vulkan_dir = engine_src / 'flutter/third_party/swiftshader/src/Vulkan'
        commit_h = swiftshader_vulkan_dir / 'commit.h'
        if not commit_h.exists():
            # 使用一个固定的commit hash stub
            commit_h.write_text('#define SWIFTSHADER_GIT_HASH "flutter-engine-build"\n')
            logger.info(f"✓ Created swiftshader commit.h stub")


    def patch(self, *, file, path):
        repo = git.Repo(path)
        repo.git.apply([file])

    def configure(
        self,
        arch: str,
        mode: str,
        api: int = 26,
        root: str = None,
        sysroot: str = None,
        toolchain: str = None,
    ):
        root = root or self.root
        sysroot = os.path.abspath(sysroot or self.sysroot.path)
        toolchain = os.path.abspath(toolchain or self.toolchain)
        cmd = [
            'vpython3',
            'engine/src/flutter/tools/gn',
            '--linux',
            '--linux-cpu', arch,
            '--enable-fontconfig',
            '--no-goma',
            '--no-backtrace',
            '--clang',
            '--lto',
            '--no-enable-unittests',
            '--no-build-embedder-examples',
            '--no-prebuilt-dart-sdk',
            '--target-toolchain', toolchain,
            '--runtime-mode', mode,
            '--no-build-glfw-shell',
            '--gn-args', 'symbol_level=0',
            '--gn-args', 'arm_use_neon=false',
            '--gn-args', 'arm_optionally_use_neon=true',
            '--gn-args', 'dart_include_wasm_opt=false',
            '--gn-args', 'dart_platform_sdk=false',
            '--gn-args', 'is_desktop_linux=false',
            '--gn-args', 'use_default_linux_sysroot=false',
            '--gn-args', 'dart_support_perfetto=false',
            '--gn-args', 'skia_use_perfetto=false',
            '--gn-args', f'custom_sysroot="{sysroot}"',
            '--gn-args', 'is_termux=true',
            '--gn-args', f'is_termux_host={utils.__TERMUX__}',
            '--gn-args', f'termux_api_level={api}',
            '--gn-args', 'custom_target_triple="aarch64-linux-android"',
            # Enable ccache for faster incremental builds
            '--gn-args', 'cc_wrapper="ccache"',
        ]
        subprocess.run(cmd, cwd=root, check=True, stdout=True, stderr=True)

    def build(self, arch: str, mode: str, root: str = None, jobs: int = None):
        root = root or self.root
        cmd = [
            'ninja', '-C', utils.target_output(root, arch, mode),
            'flutter',
            # disable zip_archives
            # 'flutter/build/archives:artifacts',
            # 'flutter/build/archives:dart_sdk_archive',
            # 'flutter/build/archives:flutter_patched_sdk',
            # 'flutter/shell/platform/linux:flutter_gtk',
            # 'flutter/tools/font_subset',
        ]
        if jobs:
            cmd.append(f'-j{jobs}')
        subprocess.run(cmd, check=True, stdout=True, stderr=True)

    def debuild(self, arch: str, output: str = None, root: str = None, **conf):
        conf = conf or self.package
        root = root or self.root
        output = output or self.output(arch)

        pkg = Package(root=root, arch=arch, **conf)
        pkg.debuild(output=output)

    def output(self, arch: str):
        if self.release.is_dir():
            name = f'flutter_{self.tag}_{utils.termux_arch(arch)}.deb'
            return self.release/name
        else:
            return self.release

    # TODO: check gclient and ninja existence
    def __call__(self):
        self.config()
        self.clone()
        self.sync()
        self.setup_termux()  # 创建 termux 配置文件

        for arch in self.arch:
            self.sysroot(arch=arch)
            for mode in self.mode:
                self.configure(arch=arch, mode=mode)
                self.build(arch=arch, mode=mode)
            self.debuild(arch=arch, output=self.output(arch))


if __name__ == '__main__':
    logger.remove()
    logger.add(
        sys.stdout,
        diagnose=False,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <9}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>")
        )
    fire.Fire(Build())
