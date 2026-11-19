cmake_minimum_required(VERSION 3.16)
get_filename_component(WMP_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(ENV{BTSTACK_ROOT} "${WMP_ROOT}/third_party/btstack")
set(EXTRA_COMPONENT_DIRS "${WMP_ROOT}/third_party/btstack/port/esp32/components" "${WMP_ROOT}/firmware/components")
set(COMPONENTS main btstack btstack_config wmp_protocol wmp_codecs)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(BTSTACK_ROOT "${WMP_ROOT}/third_party/btstack")
project(wmp_audio)
idf_component_get_property(BT_LIB btstack COMPONENT_LIB)
target_sources(${BT_LIB} PRIVATE "${CMAKE_CURRENT_LIST_DIR}/main/vhci_space.c")
get_target_property(BT_SOURCES ${BT_LIB} SOURCES)
list(FILTER BT_SOURCES EXCLUDE REGEX "/src/mesh/")
set_property(TARGET ${BT_LIB} PROPERTY SOURCES ${BT_SOURCES})
# The pinned host port stores a two-byte record length before the H4 packet.
# Reserve that tag in its admission check without modifying upstream source.
set_source_files_properties("${WMP_ROOT}/third_party/btstack/port/esp32/components/btstack/hci_transport_esp32_vhci.c"
    TARGET_DIRECTORY ${BT_LIB} PROPERTIES COMPILE_DEFINITIONS
    "WMP_VHCI_TRANSPORT_PROBE=1"
    COMPILE_OPTIONS "-include;${CMAKE_CURRENT_LIST_DIR}/main/vhci_probe.h")
