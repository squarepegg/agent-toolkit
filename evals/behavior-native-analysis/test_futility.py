"""The stopping bound must agree with every possible completion, not just the observed outcomes."""
import importlib.util
import itertools
import unittest
from pathlib import Path

s=importlib.util.spec_from_file_location("futility",Path(__file__).with_name("futility.py"));F=importlib.util.module_from_spec(s);s.loader.exec_module(F)


class Futility(unittest.TestCase):
    def test_bound_matches_exhaustive_possible_completions(self):
        n=5;alpha=.00625
        for seen in range(n+1):
            for sp,bp,cp in itertools.product(range(seen+1),repeat=3):
                possible=any(max(F.N.B.V.fisher_greater(s,n,b,n),F.N.B.V.fisher_greater(s,n,c,n))<=alpha
                             for s,b,c in itertools.product(range(sp,sp+n-seen+1),range(bp,bp+n-seen+1),range(cp,cp+n-seen+1)))
                self.assertEqual(F.best_case(sp,seen,bp,cp,n,alpha)["could_meet_significance_rule"],possible,(seen,sp,bp,cp))

    def test_one_comparator_success_rules_out_adoption_at_registered_n(self):
        self.assertFalse(F.best_case(1,1,1,0,5,.00625)["could_meet_significance_rule"])
        self.assertTrue(F.best_case(1,1,0,0,5,.00625)["could_meet_significance_rule"])

    def test_invalid_counts_are_refused(self):
        for counts in [(-1,0,0,0),(2,1,0,0),(0,6,0,0),(0,0,6,0)]:
            with self.assertRaises(ValueError):F.best_case(*counts,5,.00625)


if __name__=="__main__":unittest.main()
