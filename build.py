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
