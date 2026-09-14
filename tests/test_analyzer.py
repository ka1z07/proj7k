import unittest
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.analyzer import (
    compute_chord_signature,
    compute_hand_partition,
    analyze_slice,
    SliceAnalysis,
    PrimitiveState,
)


class TestChordSignature(unittest.TestCase):
    def test_single_notes(self):
        # L3 is col 0 (outer)
        sig_l3 = compute_hand_partition([0])
        self.assertEqual(sig_l3, "L{3}[outer]")

        # S is col 3
        sig_s = compute_hand_partition([3])
        self.assertEqual(sig_s, "S")

        # R1 is col 4 (inner)
        sig_r1 = compute_hand_partition([4])
        self.assertEqual(sig_r1, "R{1}[inner]")

    def test_adjacent_chords(self):
        # R1 + R2 = col 4, 5 (adjacent)
        sig_adj = compute_hand_partition([4, 5])
        self.assertEqual(sig_adj, "R{1,2}[adj]")

        # L3 + L2 = col 0, 1 (adjacent)
        sig_ladj = compute_hand_partition([0, 1])
        self.assertEqual(sig_ladj, "L{3,2}[adj]")

    def test_gap_chords(self):
        # L3 + L1 = col 0, 2 (gap:1, hollow middle finger)
        sig_gap1 = compute_hand_partition([0, 2])
        self.assertEqual(sig_gap1, "L{3,1}[gap:1]")

        # R1 + R3 = col 4, 6 (gap:1)
        sig_rgap1 = compute_hand_partition([4, 6])
        self.assertEqual(sig_rgap1, "R{1,3}[gap:1]")

    def test_full_hand(self):
        # R1 + R2 + R3 = col 4, 5, 6 (full)
        sig_full = compute_hand_partition([4, 5, 6])
        self.assertEqual(sig_full, "R{1,2,3}[full]")

        # L3 + L2 + L1 = col 0, 1, 2 (full)
        sig_lfull = compute_hand_partition([0, 1, 2])
        self.assertEqual(sig_lfull, "L{3,2,1}[full]")

    def test_full_chord_signature(self):
        # (2,1,2)[L3, L1, S, R2, R3] -> col 0, 2, 3, 5, 6
        sig = compute_chord_signature([0, 2, 3, 5, 6])
        self.assertEqual(sig, "(2,1,2) L{3,1}[gap:1] | S | R{2,3}[adj]")


class TestSliceAnalysis(unittest.TestCase):
    def setUp(self):
        self.bm = Beatmap7K(
            title="Test Map",
            artist="Tester",
            creator="Unit",
            version="7K",
            timing_points=[
                TimingPoint(time=0, beat_length=400.0, uninherited=True) # 150 BPM
            ],
            hit_objects=[
                # t=0: L3 LN (0~300ms), R1 Rice, R2 Rice
                HitObject(column=0, time=0, note_type=NoteType.LN, end_time=300),
                HitObject(column=4, time=0, note_type=NoteType.RICE),
                HitObject(column=5, time=0, note_type=NoteType.RICE),
                # t=100: S Rice
                HitObject(column=3, time=100, note_type=NoteType.RICE),
                # t=200: L1 LN (200~400ms)
                HitObject(column=2, time=200, note_type=NoteType.LN, end_time=400),
                # t=300: R3 Rice (L3 releases at 300)
                HitObject(column=6, time=300, note_type=NoteType.RICE),
            ]
        )

    def test_analysis_metrics(self):
        result = analyze_slice(self.bm, start_time=0, end_time=400)
        self.assertIsInstance(result, SliceAnalysis)
        self.assertEqual(result.total_notes, 6)
        self.assertEqual(result.rice_count, 4)
        self.assertEqual(result.ln_count, 2)
        # Mean locked fingers should be > 0 due to LN holds
        self.assertGreater(result.mean_locked_fingers, 0.5)

    def test_vsdl_matrix_reading_upward(self):
        result = analyze_slice(self.bm, start_time=0, end_time=400)
        vsdl_text = result.vsdl_dsl
        self.assertIn("[L3  L2  L1 | S | R1  R2  R3]", vsdl_text)
        # Check that 0ms is near the bottom and 400ms/300ms is near the top
        lines = [l for l in vsdl_text.splitlines() if l.startswith("|")]
        self.assertTrue(len(lines) >= 3)
        # In bottom-to-top, the first data row in string is latest time (future)
        # and the last data row in string is t=0 (earliest)
        self.assertIn("0ms", lines[-1])


if __name__ == "__main__":
    unittest.main()
