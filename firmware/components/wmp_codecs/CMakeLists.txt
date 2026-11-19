# Non-SBC A2DP decoders, pinned from WillyBilly06/esp32-hifi-a2dp-sink
# @ 7dcb71a0c3ffea973322fcec1f8f46abecd6b079 (third_party/esp32-hifi-a2dp-sink, sparse checkout).
#   Helix AAC (RealNetworks RPSL), libfreeaptx (LGPL-2.1-or-later).
get_filename_component(WMP_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
set(EXT "${WMP_ROOT}/third_party/esp32-hifi-a2dp-sink/esp-idf/components/bt/host/bluedroid/external")
file(GLOB HELIX_SRCS "${EXT}/libaac-lc/libhelix_aac/*.c")
# AAC-LC only: the SBR (HE-AAC) sources need headers outside the pinned subset and are not used.
list(FILTER HELIX_SRCS EXCLUDE REGEX "/sbr[^/]*[.]c$")
idf_component_register(
    SRCS "wmp_codecs.c" ${HELIX_SRCS} "wmp_aptx_backend.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "${EXT}/libaac-lc/libhelix_aac" "${EXT}/libaac-lc/libhelix_aac/utils" "${EXT}/libfreeaptx/main"
    PRIV_REQUIRES wmp_protocol heap freertos esp_timer)
# Third-party decoder sources keep their upstream style; do not fail the build on their warnings.
set_source_files_properties(${HELIX_SRCS} "wmp_aptx_backend.c" PROPERTIES COMPILE_OPTIONS "-w;-O2")
# Helix selects its ESP32 platform arithmetic from this macro.
target_compile_definitions(${COMPONENT_LIB} PRIVATE ESP32=1)
