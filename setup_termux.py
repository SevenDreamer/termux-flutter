#!/usr/bin/env python3
"""
Setup Termux build config for Flutter Engine.
This script creates the necessary termux config files after gclient sync.
"""

import os
import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: setup_termux.py <engine_src_dir>")
        sys.exit(1)
    
    engine_src = sys.argv[1]
    
    # Create build/config/termux/ directory
    termux_config_dir = os.path.join(engine_src, "build/config/termux")
    os.makedirs(termux_config_dir, exist_ok=True)
    
    # Create termux.gni
    termux_gni = os.path.join(termux_config_dir, "termux.gni")
    with open(termux_gni, "w") as f:
        f.write("""declare_args() {
  termux_api_level = 26
  is_termux = false
  is_termux_host = false
}
""")
    
    # Create BUILD.gn for termux config
    termux_build_gn = os.path.join(termux_config_dir, "BUILD.gn")
    with open(termux_build_gn, "w") as f:
        f.write("""import("//build/config/termux/termux.gni")
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
""")
    
    # Create build/toolchain/termux/ directory
    termux_toolchain_dir = os.path.join(engine_src, "build/toolchain/termux")
    os.makedirs(termux_toolchain_dir, exist_ok=True)
    
    # Create toolchain BUILD.gn
    toolchain_build_gn = os.path.join(termux_toolchain_dir, "BUILD.gn")
    with open(toolchain_build_gn, "w") as f:
        f.write("""import("//build/toolchain/gcc_toolchain.gni")
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
""")
    
    print(f"✓ Created termux config in {termux_config_dir}")
    print(f"✓ Created termux toolchain in {termux_toolchain_dir}")
    
    # Now patch BUILDCONFIG.gn
    buildconfig_path = os.path.join(engine_src, "build/config/BUILDCONFIG.gn")
    if os.path.exists(buildconfig_path):
        with open(buildconfig_path, "r") as f:
            content = f.read()
        
        # Check if already patched
        if "is_termux" in content:
            print("✓ BUILDCONFIG.gn already patched")
        else:
            # Apply patches
            # 1. Replace compiler config reference
            content = content.replace(
                '"//build/config/compiler:compiler"',
                '"//build/config/termux:compiler"'
            )
            # 2. Replace runtime_library reference
            content = content.replace(
                '"//build/config/compiler:runtime_library"',
                '"//build/config/termux:runtime_library"'
            )
            # 3. Add termux.gni import and is_termux check before is_linux check
            old_linux_check = 'if (is_linux) {\n  _native_compiler_configs += [ "//build/config/linux:sdk" ]'
            new_termux_check = '''import("//build/config/termux/termux.gni")

if (is_termux) {
  _native_compiler_configs += [ "//build/config/termux:sdk" ]
} else if (is_linux) {
  _native_compiler_configs += [ "//build/config/linux:sdk" ]'''
            content = content.replace(old_linux_check, new_termux_check)
            
            # 4. Replace executable_ldconfig
            content = content.replace(
                '"//build/config/gcc:executable_ldconfig"',
                '"//build/config/termux:executable_ldconfig"'
            )
            
            # 5. Add is_termux toolchain handling
            old_custom_check = 'if (custom_toolchain != "") {\n  assert(custom_sysroot != "")\n  assert(custom_target_triple != "")\n  host_toolchain = "//build/toolchain/linux:clang_$host_cpu"'
            new_termux_toolchain = '''if (is_termux) {
  host_toolchain = "//build/toolchain/linux:clang_$host_cpu"
  set_default_toolchain("//build/toolchain/termux:$current_cpu")
} else if (custom_toolchain != "") {
  assert(custom_sysroot != "")
  assert(custom_target_triple != "")
  host_toolchain = "//build/toolchain/linux:clang_$host_cpu"'''
            content = content.replace(old_custom_check, new_termux_toolchain)
            
            with open(buildconfig_path, "w") as f:
                f.write(content)
            print(f"✓ Patched BUILDCONFIG.gn")
    
    # Patch sysroot.gni
    sysroot_gni_path = os.path.join(engine_src, "build/config/sysroot.gni")
    if os.path.exists(sysroot_gni_path):
        with open(sysroot_gni_path, "r") as f:
            content = f.read()
        
        if "is_termux" in content:
            print("✓ sysroot.gni already patched")
        else:
            # Add is_termux sysroot handling
            old_sysroot = '''if (current_toolchain == default_toolchain && target_sysroot != "") {
  sysroot = target_sysroot
} else if (is_android) {'''
            new_sysroot = '''if (current_toolchain == default_toolchain && target_sysroot != "") {
  sysroot = target_sysroot
} else if (is_termux && custom_sysroot != "") {
  sysroot = custom_sysroot
} else if (is_android) {'''
            content = content.replace(old_sysroot, new_sysroot)
            
            with open(sysroot_gni_path, "w") as f:
                f.write(content)
            print(f"✓ Patched sysroot.gni")
    
    print("\n✓ Termux setup complete!")

if __name__ == "__main__":
    main()
