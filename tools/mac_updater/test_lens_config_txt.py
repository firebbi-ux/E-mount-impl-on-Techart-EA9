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


if __name__ == "__main__":
    unittest.main()
