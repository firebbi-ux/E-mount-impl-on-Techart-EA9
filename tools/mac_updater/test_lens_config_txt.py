import unittest

import cfgblock
import lens_config_txt


class LensConfigTxtTests(unittest.TestCase):
    def test_round_trip_eight_slots(self):
        slots = [
            (35, "Sony FE 35mm F1.4 GM", 42.5),
            (85, "Sony FE 85mm F1.4 GM", 61.0),
            (0, "", 0.0),
            (24, "Sony FE 24mm F2.8 G", 39.5),
            (0, "", 0.0),
            (0, "", 0.0),
            (0, "", 0.0),
            (0, "", 0.0),
        ]
        self.assertEqual(lens_config_txt.parse_txt(lens_config_txt.format_txt(slots)), slots)

    def test_empty_template_round_trip(self):
        empty = [(0, "", 0.0)] * 8
        self.assertEqual(lens_config_txt.parse_txt(lens_config_txt.format_txt(empty)), empty)

    def test_aperture_slot_mapping_is_checked(self):
        text = lens_config_txt.format_txt([(35, "Lens", 42.5)] + [(0, "", 0.0)] * 7)
        text = text.replace("槽位号：1（机身切换镜头光圈档位：f/2.5）",
                            "槽位号：1（机身切换镜头光圈档位：f/2.8）")
        with self.assertRaises(ValueError):
            lens_config_txt.parse_txt(text)

    def test_txt_slots_convert_to_existing_firmware_block(self):
        slots = [(50, "Test Lens", 48.3)] + [(0, "", 0.0)] * 7
        block = cfgblock.pack(2, slots)
        hw, decoded = cfgblock.parse(block)
        self.assertEqual(hw, 2)
        self.assertEqual(decoded, slots)

    def test_full_flash_selects_valid_block_for_preferred_hardware_version(self):
        v2_slots = [(35, "Lens v2", 42.5)] + [(0, "", 0.0)] * 7
        v1_slots = [(50, "Lens v1", 48.3)] + [(0, "", 0.0)] * 7
        v2_block = cfgblock.pack(2, v2_slots)
        v1_block = cfgblock.pack(1, v1_slots)
        flash = bytearray(0x20000)
        flash[0xAF08:0xAF08 + len(v2_block)] = v2_block
        flash[0x12008:0x12008 + len(v1_block)] = v1_block
        # A bootloader marker is outside the application scan range.
        flash[0x100:0x100 + len(cfgblock.MAGIC)] = cfgblock.MAGIC
        self.assertEqual(lens_config_txt.slots_from_flash(bytes(flash)), v2_slots)
        self.assertEqual(
            lens_config_txt.slots_from_flash(bytes(flash), preferred_hw_version=1),
            v1_slots,
        )

    def test_full_flash_ignores_invalid_candidate(self):
        slots = [(35, "Lens at slot 1", 42.5)] + [(0, "", 0.0)] * 7
        block = cfgblock.pack(2, slots)
        flash = bytearray(0x20000)
        flash[0xAF08:0xAF08 + len(block)] = block
        flash[0x12008:0x12008 + len(cfgblock.MAGIC)] = cfgblock.MAGIC
        self.assertEqual(lens_config_txt.slots_from_flash(bytes(flash)), slots)


if __name__ == "__main__":
    unittest.main()
