import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(sys.argv.pop(1))
spec = importlib.util.spec_from_file_location('portable_tracker', ROOT / 'oashya/tracker.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DetectorTests(unittest.TestCase):
    def test_preprocess_matches_original_channel_order_scale_and_shape(self):
        image = np.zeros((720, 1280, 3), np.uint8)
        image[:] = (10, 30, 60)
        tensor = module.Predictor().preprocess(image)
        channels = np.array([60, 30, 10], np.float32).reshape(1, 3, 1, 1) / np.float32(255)
        reference = np.broadcast_to(channels, (1, 3, 640, 640))
        np.testing.assert_array_equal(tensor, reference)

    def test_invalid_detections_are_filtered_and_box_contract_preserved(self):
        outputs = [[250, 100, 350, 200, .9, 100], [0, 0, 2, 2, .1, 1],
                   [4, 0, 2, 2, .9, 1], [0, 0, 2, 2, float('nan'), 1]]
        detected = module.Predictor().postprocess(outputs)
        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0][0], 100)
        self.assertEqual(detected[0][2], [300, 150, 100, 100])

    def test_two_window_offsets_match_the_observed_desktop_tracker(self):
        tracker = module.Tracker()
        windows = []
        def predict(image):
            windows.append(image.shape)
            return [(100, .9, [250, 150, 100, 100])]
        tracker.predictor.predict = predict
        detected = tracker.detect(np.zeros((720, 1280, 3), np.uint8))
        self.assertEqual(windows, [(640, 640, 3), (640, 640, 3)])
        self.assertEqual([item[2] for item in detected], [[250, 150, 100, 100], [890, 150, 100, 100]])

    def test_nonconstant_preprocessing_matches_the_original_runtime(self):
        y, x = np.indices((720, 1280), dtype=np.int32)
        frame = np.stack(((x * 3 + y * 7) % 256, (x * 11 + y * 5) % 256,
                          (x * 7 + y * 13) % 256), axis=2).astype(np.uint8)
        with np.load(ROOT / 'oashya/detector_reference_input.npz') as data:
            np.testing.assert_array_equal(module.Predictor().preprocess(frame), data['input'])

    def test_velocity_and_identity_survive_a_missed_frame(self):
        tracker = module.Tracker()
        detection = lambda x: [(100, .9, [x, 350, 60, 80])]
        first = tracker.update(detection(500), .2)[0]
        second = tracker.update(detection(510), .2)[0]
        self.assertEqual(first[0], second[0])
        self.assertAlmostEqual(second[-1], 50)
        self.assertEqual(tracker.update([], .2), [])
        recovered = tracker.update(detection(530), .2)[0]
        self.assertEqual(first[0], recovered[0])
        self.assertAlmostEqual(recovered[-1], 50)

    def test_changed_class_does_not_reuse_a_target_identity(self):
        tracker = module.Tracker()
        first = tracker.update([(100, .9, [500, 350, 60, 80])], .2)[0]
        second = tracker.update([(101, .9, [500, 350, 60, 80])], .2)[0]
        self.assertNotEqual(first[0], second[0])

    def test_expired_targets_and_clear_do_not_appear_as_visible(self):
        tracker = module.Tracker()
        first = tracker.update([(100, .9, [500, 350, 60, 80])], .2)[0]
        self.assertEqual(tracker.update([], 1), [])
        second = tracker.update([(100, .9, [500, 350, 60, 80])], .2)[0]
        self.assertNotEqual(first[0], second[0])
        tracker.clear_tracks()
        self.assertEqual(tracker.update([], .2), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
