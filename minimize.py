import os
from typing import List,Set
from transition_system import TransitionSystem
from prime import *
from cover_constraints import CoverConstraints
from finite_ivy_instantiate import FiniteIvyInstantiator
from util import QrmOptions, ForwardMode
from verbose import *
from ivy import ivy_logic as il

class Rmin():
    # static members
    definitions  = {}
    eq_relations = []
    def_lines = []
    eq_lines  = []
    def_qcost = 0
    eq_qcost  = 0
    
    def __init__(self, solution, orbits):
        self.solution    = solution
        self.invariants  = [orbits[i].quantified_form for i in solution] 
        self.invar_lines = []
        for i in solution:
            line = f'invariant [invar_{i}] {str(orbits[i].quantified_form)} # qcost: {orbits[i].qcost}'
            self.invar_lines.append(line)
        
    @staticmethod
    def set_definitions_and_eq_relations(tran_sys : TransitionSystem):
        Rmin.definitions  = tran_sys.definitions
        Rmin.eq_relations = list(tran_sys.closed_atom_equivalence_constraints)
        for def_symbol, def_ast in tran_sys.definitions.items():
            (num_forall, num_exists, num_lits) = futil.count_quantifiers_and_literals(il.close_formula(def_ast))
            qcost = num_forall + num_exists + num_lits
            line = f'invariant [def_{str(def_symbol)}] {format(il.close_formula(def_ast))} # definition, qcost: {qcost}'
            Rmin.def_lines.append(line)
            Rmin.def_qcost += qcost
        for i, atom_equiv in enumerate(tran_sys.closed_atom_equivalence_constraints):
            (num_forall, num_exists, num_lits) = futil.count_equiv_invar_quantifiers_and_literals(atom_equiv)
            qcost = num_forall + num_exists + num_lits
            line = f'invariant [eq_{i}] {format(atom_equiv)} # equivalence relation, qcost: {qcost}'
            Rmin.eq_lines.append(line)
            Rmin.eq_qcost += qcost

def remove_target_from_source(source : list, target : set) -> list:
    temp = source.copy()
    source.clear()
    removed = []
    for x in temp:
        if x in target:
            removed.append(x)
        else:
            source.append(x)
    return removed

class StackLevel():
    def __init__(self, level: int, start_idx: int) -> None:
        self.level                = level
        self.solution_start_idx   = start_idx
        self.orbit_id             = -1 
        self.include_orbit        = True 
        self.unpended : List[int] = [] 

    def _switch_branch(self) -> None:
        self.include_orbit = not self.include_orbit

class Minimizer():
    def __init__(self, options : QrmOptions, tran_sys : TransitionSystem, instantiator : FiniteIvyInstantiator, orbits: List[PrimeOrbit]) -> None: 
        self.tran_sys      = tran_sys
        self.orbits        = orbits
        self.cover         = CoverConstraints(options, tran_sys, instantiator, orbits, options.useMC)
        self.max_cost      = 0 
        self.ubound        = 0 
        self.bnb_max_depth = 0
        self.decision_stack : List[StackLevel] = []
        self.pending    : List[int] = list(range(len(orbits)))
        self.solution   : List[int] = []
        self.optimal_solutions : List[List[int]] = []
        self.rmin          = []
        self.options = options

    #------------------------------------------------------------
    # Minimizer: minimization 
    #------------------------------------------------------------
    def _get_cost(self) -> int:
        s = sum([self.orbits[i].qcost for i in self.solution])
        vprint(self.options, f'\nSolution : {self.solution} has cost {s}.', 5)
        return s

    def _get_max_coverage_id(self) -> int:
        max_val = 0
        max_id  = -1
        for i in self.pending:
            orbit = self.orbits[i]
            coverage = self.cover.coverage[i]
            if coverage > max_val:
                max_val = coverage
                max_id  = i
        assert(max_val > 0 and max_id >=0)
        return max_id

    def _get_initial_phase(self) -> bool:
        # hot start
        return True

    def _invert_decision(self) -> None:
        assert(len(self.decision_stack))
        top = self.decision_stack[-1]
        if top.include_orbit:
            assert(top.orbit_id == self.solution.pop())
        top._switch_branch()
        if top.include_orbit:
            self.solution.append(top.orbit_id)
        vprint(self.options, f'\nInvert decision for {top.orbit_id} at level {top.level}', 5)

    def _new_level(self) -> None:
        level    = len(self.decision_stack)
        start_id = len(self.solution)
        self.bnb_max_depth = max(level, self.bnb_max_depth)
        self.decision_stack.append(StackLevel(level,start_id))
        vprint(self.options, f'\nNew level: {level}\n pending : {self.pending}\n solution : {self.solution}', 5)

    def _decide(self) -> None:
        # decide orbit id and initial phase
        assert(len(self.decision_stack))
        top = self.decision_stack[-1]
        top.orbit_id      = self._get_max_coverage_id() 
        top.include_orbit = self._get_initial_phase() 
        vprint(self.options, f'\nDecide in level {top.level} among pending : {self.pending}', 5)
        vprint(self.options, f'Coverage : {[(i,c) for (i,c) in enumerate(self.cover.coverage)]}', 5)
        vprint(self.options, f'Decide {top.orbit_id} with phase {top.include_orbit} at level {top.level}', 5)
        # update pending and solution accordingly
        top.unpended.append(top.orbit_id)
        self.pending.remove(top.orbit_id)
        if top.include_orbit:
            self.solution.append(top.orbit_id)
        vprint(self.options, f'After decision at level {top.level}\n pending : {self.pending}\n solution : {self.solution}', 5)

    def _backtrack(self) -> None:
        assert(len(self.decision_stack))
        top = self.decision_stack[-1]
        vprint(self.options,f'\nBefore backtrack at level {top.level}\n pending : {self.pending}\n solution : {self.solution}', 5)
        # restore pending and solution
        self.pending.extend(top.unpended)
        if len(self.solution) > top.solution_start_idx:
            del self.solution[top.solution_start_idx:]
        self.decision_stack.pop()
        vprint(self.options, f'After backtrack at level {top.level}\n pending : {self.pending}\n solution : {self.solution}', 5)
    
    def _collect_essentials(self) -> Set[int]:
        essentials = set()
        for i in self.pending:
            orbit = self.orbits[i]
            if(self.cover.is_essential(orbit, self.pending, self.solution)):
                essentials.add(i)
        if self.options.verbosity >=5:
            assert(len(self.decision_stack))
            top = self.decision_stack[-1]
            vprint(self.options, f'Essensial at level {top.level} : {essentials}', 5)
        return essentials

    def _collect_covered(self) -> Set[int]:
        vprint(self.options, f'Before removed\n coverage : {[(i,c) for (i,c) in enumerate(self.cover.coverage)]}', 5)
        covered = set()
        self.cover.reset_coverage()
        for i in self.pending:
            orbit = self.orbits[i]
            coverage = self.cover.get_coverage(orbit, self.solution)
            if coverage == 0:
                covered.add(i)
        if self.options.verbosity >=5:
            assert(len(self.decision_stack))
            top = self.decision_stack[-1]
            vprint(self.options, f'After removed\n coverage : {[(i,c) for (i,c) in enumerate(self.cover.coverage)]}', 5)
            vprint(self.options, f'Covered at level {top.level} : {covered}', 5)
        return covered

    def _unpend(self, to_unpend : Set[int]) -> None:
        removed = remove_target_from_source(source=self.pending, target=to_unpend) 
        assert(len(self.decision_stack))
        top = self.decision_stack[-1]
        top.unpended.extend(removed)
    
    def _add_essentials(self) -> bool:
        essentials = self._collect_essentials()
        self.solution += list(essentials)
        self._unpend(essentials)
        return len(essentials) > 0
    
    def _remove_covered(self) -> bool:
        covered = self._collect_covered()
        self._unpend(covered)
        return len(covered) > 0

    def _reduce(self) -> None:
        vprint(self.options, f'\nBefore reduction : \n pending  : {self.pending}\n solution : {self.solution}', 5)
        has_essential = self._add_essentials()
        has_covered   = self._remove_covered()
        vprint(self.options, f'After reduction : \n pending  : {self.pending}\n solution : {self.solution}', 5)
        if has_essential or has_covered:
            self._reduce()

    def _solve_one(self) -> int: 
        self._new_level()
        self._reduce() 
        cost = self._get_cost()
        if len(self.pending) == 0: 
            if cost < self.ubound:
                self.ubound = cost
                self.optimal_solutions = [self.solution.copy()] 
                self._backtrack()
                return cost 
            else:
                self._backtrack()
                return self.max_cost 
        if cost >= self.ubound:
            self._backtrack()
            return self.max_cost
        self._decide()
        cost1 = self._solve_one()
        if(cost1 == cost):
            self._backtrack()
            return cost1
        self._invert_decision()
        cost2 = self._solve_one()
        self._backtrack()
        return min(cost1,cost2)

    def _solve_all(self) -> None:
        self._new_level()
        self._reduce() 
        cost = self._get_cost()
        if len(self.pending) == 0: 
            if cost < self.ubound:
                self.ubound = cost
                self.optimal_solutions = [self.solution.copy()] 
                self._backtrack()
                return 
            elif cost == self.ubound:
                self.optimal_solutions.append(self.solution.copy()) 
                self._backtrack()
                return 
        if cost > self.ubound:
            self._backtrack()
            return
        self._decide()
        self._solve_all()
        self._invert_decision()
        self._solve_all()
        self._backtrack()
        return 

    #------------------------------------------------------------
    # Minimizer: reduction 
    #------------------------------------------------------------
    def _remove_definition_prime_orbits_from_pending(self) -> Set[int]:
        def_orbits : Set[int]  = set()
        for orbit_id in self.pending:
            orbit = self.orbits[orbit_id]
            is_definition = self.cover.is_definition_prime(orbit)
            if (is_definition):
                def_orbits.add(orbit_id)
        vprint(self.options, f'definition primes: {def_orbits}', 5)
        remove_target_from_source(source=self.pending, target=def_orbits)

    #------------------------------------------------------------
    # Minimizer: helpers 
    #------------------------------------------------------------
    def _print_quantifier_inference(self, inference_list) -> None:
        if self.options.writeQI:
            prime_filename   = self.options.instance_name + '.' + self.options.instance_suffix + '.qpis'
            outF = open(prime_filename, "w")
            for i in inference_list:
                orbit = self.orbits[i]
                outF.write(str(orbit))
            outF.close()
        vprint_step_banner(self.options, f'[QI RESULT]: Quantified Prime Orbits on [{self.options.ivy_filename}: {self.options.size_str}]', 3)
        for i in inference_list:
            orbit = self.orbits[i]
            vprint(self.options, str(orbit), 3)

    def print_rmin(self) -> None:
        vprint_step_banner(self.options, f'[MIN RESULT]: Minimized Invariants on [{self.options.ivy_filename}: {self.options.size_str}]', 3)
        vprint(self.options, f'[MIN NOTE]: number of minimal solution found: {len(self.optimal_solutions)}', 3)
        vprint(self.options, f'[MIN NOTE]: upper bound: {self.ubound}', 3)
        vprint(self.options, f'[MIN NOTE]: maximum branch and bound depth: {self.bnb_max_depth}', 3)
        vprint(self.options, f'[MIN NOTE]: number of definitions: {len(Rmin.def_lines)}', 3)
        for line in Rmin.def_lines:
            vprint(self.options, line, 3)
        vprint(self.options, f'[MIN NOTE]: number of equality relations: {len(Rmin.eq_lines)}', 3)
        for line in Rmin.eq_lines:
            vprint(self.options, line, 3)
        for (sid, rmin) in enumerate(self.rmin):
            vprint(self.options, f'[MIN NOTE]: Solution {sid} : {rmin.solution}', 3)
            vprint(self.options, f'[MIN NOTE]: solution length: {len(rmin.solution)}', 3)
            for line in rmin.invar_lines:
                vprint(self.options, line, 3)
            vprint(self.options, f'[MIN NOTE]: number of total invariants: {len(rmin.solution) + len(Rmin.def_lines) + len(Rmin.eq_lines)}', 3)
            vprint(self.options, f'[MIN NOTE]: total qCost: {self.ubound + Rmin.def_qcost + Rmin.eq_qcost}', 3)
            vprint(self.options, '\n', 3)

    def set_rmin(self) -> None:
        Rmin.set_definitions_and_eq_relations(self.tran_sys)
        for solution in self.optimal_solutions:
            self.rmin.append(Rmin(solution, self.orbits))

    def write_ivy_files(self) -> None:
        for rmin_id, rmin in enumerate(self.rmin):
            ivy_name = self.options.instance_name + '.' + self.options.instance_suffix + f'.{rmin_id}'+ '.ivy'
            cp_cmd = f'cp {self.options.ivy_filename} {ivy_name}'
            os.system(cp_cmd)
            comment_invar_cmd = f"sed -i '/invariant/s/^/#/' {ivy_name}"
            os.system(comment_invar_cmd) # comment out the existing invariants, including safety property
            ivy_file = open(ivy_name, 'a')
            ivy_file.write('\n')
            invariants = Rmin.def_lines + Rmin.eq_lines + rmin.invar_lines
            for line in invariants:
                ivy_file.write(line+'\n')
            ivy_file.close()

    #------------------------------------------------------------
    # Minimizer: public core methods
    #------------------------------------------------------------
    def reduce_redundant_prime_orbits(self):
        self._remove_definition_prime_orbits_from_pending()
        self._new_level()
        self._reduce()

    def quantifier_inference(self, instantiator: FiniteIvyInstantiator, atoms) -> None:
        from qinference import QInference, QPrime
        QInference.setup(atoms, self.tran_sys, instantiator)
        vprint_title(self.options, 'quantifier_inference', 5)
        inference_list = self.solution + self.pending
        for orbit_id in inference_list:
            orbit = self.orbits[orbit_id]
            vprint(self.options, str(orbit), 5)
            qinf    = QInference(orbit, self.options)
            qclause = qinf.get_qclause()
            orbit.set_quantifier_inference_result(qclause)
            if self.options.sanity_check:
                self.cover.init_quantifier_inference_check_solver_smt(orbit.primes, qclause)
                vprint_title(self.options, f'Quantifier Inference: orbit {orbit_id}')
                if self.cover.quantifier_inference_check_smt():
                    vprint(self.options, f'[QI_CHECK RESULT]: PASS')
                else:
                    vprint(self.options, f'[QI_CHECK RESULT]: FAIL')
        # output result
        self._print_quantifier_inference(inference_list)
        self.max_cost = 1 + sum([orbit.qcost for orbit in self.orbits])
        self.ubound   = self.max_cost

    def solve_rmin(self) -> List[str]:
        if self.options.all_solutions:
            self._solve_all()
        else:
            self._solve_one()
        self.set_rmin()
        self.print_rmin()
        self.write_ivy_files()

    def _compare_symmetry_quotient(self, sol_id, invariants, protocol : Protocol):
        vprint(self.options, f'Minimization check for Solution {sol_id}')
        self.cover.init_minimization_check_solver(invariants, protocol)
        (result, values)  = self.cover.get_minimization_check_minterm()
        model_repr_states = set()
        model_match = True
        while result:
            repr_int = int(''.join(values), 2)
            for nvalues in protocol.all_permutations(values[:protocol.state_atom_num]): # only permute the mutable part
                nvalues += values[protocol.state_atom_num:]
                repr_int = min(int(''.join(nvalues), 2), repr_int)
                self.cover.block_minimization_check_minterm(nvalues)
            if not repr_int in protocol.repr_states:
                bit_str = '{0:0{1}b}'.format(repr_int, protocol.atom_num)[:protocol.state_atom_num]
                vprint(self.options, f'Found a representative state in Rmin not in reachability: decimal: {repr_int}, binary: {bit_str}')
                model_match = False 
            model_repr_states.add(repr_int)
            (result, values) = self.cover.get_minimization_check_minterm()

        difference = protocol.repr_states - model_repr_states
        if len(difference) > 0:
            vprint(self.options, 'Found states in reachability not in Rmin')
            vprint(self.options, f'{difference}')
            model_match = False
        if model_match:
            vprint(self.options, f'[MIN_CHECK RESULT]: PASS')
        else:
            vprint(self.options, f'[MIN_CHECK RESULT]: FAIL')
        return model_match 

    def _equivalence_checking(self, sol_id, invariants, protocol : Protocol) -> bool:
        vprint(self.options, f'Minimization check for Solution {sol_id}')
        self.cover.init_minimization_check_solver(invariants, protocol)
        (result, _)  = self.cover.get_minimization_check_minterm()
        if result: # Non-equal
            vprint(self.options, f'[MIN_CHECK RESULT]: FAIL')
            return False
        else:
            vprint(self.options, f'[MIN_CHECK RESULT]: PASS')
            return True

    def minimization_check(self, protocol : Protocol):
        self.cover.init_minimization_check_clauses()
        result = True
        for sol_id, solution in enumerate(self.optimal_solutions):
            invariants = [self.orbits[orbit_id].quantified_form for orbit_id in solution]
            if self.options.forward_mode == ForwardMode.Sym_DFS:
                result = result and self._compare_symmetry_quotient(sol_id, invariants, protocol)
            else:
                result = result and self._equivalence_checking(sol_id, invariants, protocol)
        return result