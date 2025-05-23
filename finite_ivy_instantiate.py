from itertools import product

import re
from ivy import ivy_logic as il
from ivy import ivy_logic_utils as ilu
from ivy import logic as lg
from verbose import *
from transition_system import TransitionSystem

from enum import Enum
InstantiateMode  = Enum('InstantiateMode', ['vars', 'equals', 'non_bools'])

class FiniteIvyInstantiator():
    def __init__(self, tran_sys : TransitionSystem):
        self._tran_sys     = tran_sys
        self._interpreted_vars  = []  
        self._state_vars        = []  
        self._indep_vars        = [] # non-defined state vars 

        self._initialize()

        # instantiated
        # contains atomic predicate or non-boolean output functions
        self._instantiated_interpreted_vars = [] 
        self._instantiated_indep_vars       = []
        # contains either atomic predicate or equality term (f=g)
        self._instantiated_interpreted_equals = [] 
        self._instantiated_state_equals       = []
        # contains non-boolean output functions only
        self._instantiated_indep_non_bools  = []
        # actions
        self._instantiated_actions          = []

        self._instantiate()


        # public member 
        # dfs
        self.dfs_state_vars          = []
        self.dfs_interpreted_vars    = []
        # protocol
        self.protocol_state_atoms             = []
        self.protocol_interpreted_atoms       = []
        self.protocol_state_atoms_fmlas       = []        
        self.protocol_interpreted_atoms_fmlas = []
        self.protocol_atoms                   = []
        self.protocol_atoms_fmlas             = []
        # ivy
        self.ivy_state_vars          = []
        self.ivy_non_bool_state_vars = {} 
        self.ivy_actions             = []
        # dependent axioms
        self.dep_axioms_fmla             = []
        self.dep_axioms_str              = []
        # definitions
        self.instantiated_def_map    = {}
        # axioms
        self.axiom_fmla = None

        self._set_pretty_instantiations()

    def _init_interpreted_vars(self):
        self._interpreted_vars = list(self._tran_sys.interpreted_symbols)
        self._interpreted_vars.sort(key=lambda v: str(v))

    def _init_state_vars(self):
        self._state_vars = list(self._tran_sys.state_symbols)
        self._state_vars.sort(key=lambda v: str(v))

    def _init_independent_vars(self):
        for symbol in self._tran_sys.state_symbols:
            assert(symbol not in self._tran_sys.interpreted_symbols)
            if (symbol not in self._tran_sys.definitions.keys()):
                self._indep_vars.append(symbol)
        self._indep_vars.sort(key=lambda v: str(v))

    def _initialize(self):
        self._init_interpreted_vars()
        self._init_state_vars()
        self._init_independent_vars()

    def _get_all_params_of_param_types(self, param_types):
        elems = []
        for ptype in param_types:
            if il.is_boolean_sort(ptype):
                elems.append([il.And(), il.Or()])
            else:
                elems.append(self._tran_sys.sort2consts[ptype])
        all_params = list(product(*elems))
        all_params = [list(params) for params in all_params]
        return all_params

    def _get_instantiated_variables(self, var, var_type, mode):
        if mode == InstantiateMode.vars:
            return [var]
        elif mode == InstantiateMode.equals:
            if il.is_boolean_sort(var_type):
                 return [var]
            eq_symbols = []
            for elem in self._tran_sys.sort2consts[var_type]:
                eq_symbols.append(il.Equals(var, elem))
            return eq_symbols
        else: # mode == InstantiateMode.non_bools
            if il.is_boolean_sort(var_type):
                return []
            else:
                return [(var, var_type)]

    def _get_instantiated_functions(self, func_symbol, mode):
        func_type   = func_symbol.sort
        param_types = func_type.dom
        return_type = func_type.rng
        all_params  = self._get_all_params_of_param_types(param_types)
        functions  = []
        for params in all_params:
            func  = il.apply(func_symbol, params)
            funcs = self._get_instantiated_variables(func, return_type, mode)
            functions += funcs
        return functions 

    def _get_instantiated_terms(self, terms, mode):
        instantiated_terms = []
        for var in terms:
            var_type = var.sort
            if il.is_function_sort(var_type):
                instantiated_terms += self._get_instantiated_functions(var, mode)
            else: 
                instantiated_terms += self._get_instantiated_variables(var, var_type, mode)
        return instantiated_terms

    def _get_all_parameterized_actions(self):
        actions = self._tran_sys.exported_action_symbols
        parameterized_actions = []
        for action in actions:
            parameterized_actions += self._get_instantiated_functions(action, mode=InstantiateMode.vars)
        return parameterized_actions

    def _instantiate(self):
        self._instantiated_interpreted_vars   = self._get_instantiated_terms(terms=self._interpreted_vars, mode=InstantiateMode.vars)
        self._instantiated_indep_vars       = self._get_instantiated_terms(terms=self._indep_vars,     mode=InstantiateMode.vars)
        self._instantiated_interpreted_equals = self._get_instantiated_terms(terms=self._interpreted_vars, mode=InstantiateMode.equals)
        self._instantiated_state_equals     = self._get_instantiated_terms(terms=self._state_vars,     mode=InstantiateMode.equals)
        self._instantiated_indep_non_bools  = self._get_instantiated_terms(terms=self._indep_vars,     mode=InstantiateMode.non_bools)
        self._instantiated_actions          = self._get_all_parameterized_actions()

    def _get_dfs_variables_from_instantiated_equals(self, instantiated_equals):
        pretty_equals   = []
        for equal in instantiated_equals:
            pretty_equals.append(str(equal).replace('.', '__'))

        pretty_dfs_vars = []
        for var in pretty_equals:
            symbol_name = '' 
            args        = []
            match_predicate = re.search(r'(\w+)\(([^)]+)\)',         var)
            match_eq        = re.search(r'(\w+)\s*=\s*(\w+)',    var)
            match_func_eq   = re.search(r'(\w+)\((\w+)\) = (\w+)', var)
            if match_func_eq: # case 4: general function
                symbol_name = match_func_eq.group(1)
                args        = match_func_eq.group(2).split(', ') + match_func_eq.group(3).split(', ')
            elif match_predicate: # case 3: predicate 
                symbol_name = match_predicate.group(1)
                args        = match_predicate.group(2).split(',') + ['true']
            elif match_eq: # case 1
                symbol_name = match_eq.group(1)
                args        = match_eq.group(2).split(',')
            else: # case 2: bool
                symbol_name = var.strip('( )')            
                args        = ['true']
            pretty_var = symbol_name + '(' + ','.join(args) + ')'
            pretty_dfs_vars.append(pretty_var)
        return pretty_dfs_vars

    def _set_dfs_variables(self):
        self.dfs_state_vars     = self._get_dfs_variables_from_instantiated_equals(self._instantiated_state_equals)
        self.dfs_interpreted_vars = self._get_dfs_variables_from_instantiated_equals(self._instantiated_interpreted_equals)

    def _set_protocol_atoms(self):
        for atom in self._instantiated_state_equals:
            self.protocol_state_atoms.append(str(atom))
        self.protocol_state_atoms_fmlas = self._instantiated_state_equals
        for atom in self._instantiated_interpreted_equals:
            self.protocol_interpreted_atoms.append(str(atom))
        self.protocol_interpreted_atoms_fmlas = self._instantiated_interpreted_equals
        self.protocol_atoms       = self.protocol_state_atoms + self.protocol_interpreted_atoms
        self.protocol_atoms_fmlas = self.protocol_state_atoms_fmlas + self.protocol_interpreted_atoms_fmlas
    
    def _set_ivy_variables(self):
        ivy_vars      = self._instantiated_indep_vars
        ivy_non_bools = self._instantiated_indep_non_bools
        for var in ivy_vars:
            self.ivy_state_vars.append(str(var).replace('.', '__'))
        for non_bool in ivy_non_bools:
            var = str(non_bool[0]).replace('.', '__')
            var_type = self._tran_sys.get_sort_name_from_finite_sort(non_bool[1])
            self.ivy_non_bool_state_vars[var] = var_type

    def _set_ivy_actions(self):
        actions = self._instantiated_actions
        for action in actions:
            self.ivy_actions.append(str(action))

    def _set_dependent_axioms(self):
        for set_sort in self._tran_sys.dep_types.keys():
            dep_relation = self._tran_sys.get_dependent_relation(set_sort)
            dep_sets     = self._tran_sys.get_dependent_sets(set_sort)
            dep_elems    = self._tran_sys.get_dependent_elements(set_sort)
            for set_id, dep_set in enumerate(dep_sets):
                elems_in_set   = self._tran_sys.get_dependent_elements_in_set(set_sort, set_id)
                for elem in dep_elems:
                    args = [elem, dep_set]
                    dep_symbol = il.apply(dep_relation, args)
                    if elem not in elems_in_set:
                        dep_symbol = il.Not(dep_symbol)
                    self.dep_axioms_fmla.append(dep_symbol)
        self.dep_axioms_str = [str(axiom) for axiom in self.dep_axioms_fmla]

    def _get_var_substitution(self, var_list):
        vars_domain = []
        for var in var_list:
            var_sort   = var.sort
            var_domain = self._tran_sys.sort2consts[var_sort]
            vars_domain.append(var_domain)
        var_subst_candidates = list(product(*vars_domain))
        substition_maps = [] 
        for subst_candidate in var_subst_candidates:
            subst_map = {}
            for i, var in enumerate(var_list):
                subst_map[var] = subst_candidate[i]
            substition_maps.append(subst_map)
        return substition_maps

    def _recursive_instantiate_quantifier(self, formula):
        if il.is_quantifier(formula):
            prefix_vars = il.quantifier_vars(formula)
            if len(prefix_vars) > 0:
                instantiated_matrix = self._recursive_instantiate_quantifier(formula.body)
                prefix_subst_maps   = self._get_var_substitution(prefix_vars)
                instantiated_fmlas  = [il.substitute(instantiated_matrix, subst) for subst in prefix_subst_maps]
                if il.is_exists(formula):
                    formula = il.Or(*instantiated_fmlas)
                else:
                    formula = il.And(*instantiated_fmlas)
            else:
                for i, arg in enumerate(formula.args):
                    instantiated_arg = self._recursive_instantiate_quantifier(arg)
                    formula._content.args[i] = instantiated_arg
        else:
            args = [self._recursive_instantiate_quantifier(arg) for arg in formula.args]
            if isinstance(formula, il.Not):
                formula = il.Not(args[0])
            elif isinstance(formula, il.Or):
                formula = il.Or(*args)
            elif isinstance(formula, il.And):
                formula = il.And(*args)
            elif isinstance(formula, il.Implies):
                formula = il.Implies(args[0], args[1])
            elif isinstance(formula, lg.Eq):
                formula = il.Equals(args[0], args[1])
        return formula

    def recursive_simplify(self, formula):
        assert(not il.is_quantifier(formula))
        args = [self.recursive_simplify(arg) for arg in formula.args]
        if isinstance(formula, il.Not):
            if il.is_true(args[0]):
                 return il.Or()
            elif il.is_false(args[0]):
                return il.And()
            formula = il.Not(args[0])
        elif isinstance(formula, il.Or):
            reduced_args = [] 
            for arg in args:
                if il.is_true(arg):
                    return il.And()
                elif il.is_false(arg):
                    continue
                reduced_args.append(arg)
            formula = il.Or(*reduced_args)
        elif isinstance(formula, il.And):
            reduced_args = []
            for arg in args:
                if il.is_false(arg):
                    return il.Or()
                elif il.is_true(arg):
                    continue
                reduced_args.append(arg)
            formula = il.And(*reduced_args)
        elif isinstance(formula, il.Implies):
            if il.is_true(args[0]):
                return args[1]
            elif il.is_false(args[0]):
                return il.And()
            formula = il.Implies(args[0], args[1])
        elif isinstance(formula, lg.Eq):
            if il.is_enumerated(args[0]) and il.is_enumerated(args[1]):
                constants = set(args[0].sort.extension)
                if str(args[0]) in constants and str(args[1]) in constants: 
                    if args[0] == args[1]:
                        return il.And()
                    else:
                        return il.Or()
                elif str(args[0]) in constants:
                    return il.Equals(args[1], args[0])
                elif str(args[1]) in constants:
                    return il.Equals(args[0], args[1])
                else:
                    assert(0) 
            elif (il.is_true(args[0]) or il.is_false(args[0])) and (il.is_true(args[1]) or il.is_false(args[1])):
                return il.And() if args[0] == args[1] else il.Or()
            elif il.is_true(args[0]) or il.is_false(args[0]):
                return args[1] if il.is_true(args[0]) else il.Not(args[1])
            elif  il.is_true(args[1]) or il.is_false(args[1]):
                return args[0] if il.is_true(args[1]) else il.Not(args[0])
            formula = il.Equals(args[0], args[1])
        elif isinstance(formula, il.Ite):
            if il.is_true(args[0]):
                return args[1]
            elif il.is_false(args[0]):
                return args[2]
            formula = il.Ite(args[0], args[1], args[2])
        return formula

    def instantiate_quantifier(self, formula):
        formula = self._recursive_instantiate_quantifier(formula)
        formula = self.recursive_simplify(formula)
        return formula

    def _set_definitions(self):
        def_map = self._tran_sys.definitions
        for def_ast in def_map.values():
            def_lhs = def_ast.lhs()  # didNotVote(N)
            def_rhs = def_ast.rhs()  # forall V. ~vote(N,V)
            def_lhs_free_vars = def_lhs.terms # N
            var_subst_maps = self._get_var_substitution(def_lhs_free_vars)
            def_rhs  = self.instantiate_quantifier(def_rhs)
            for subst_map in var_subst_maps:
                instantiated_lhs = il.substitute(def_lhs, subst_map)
                instantiated_rhs = il.substitute(def_rhs, subst_map)
                self.instantiated_def_map[instantiated_lhs] = instantiated_rhs

    def _set_axioms(self):
        axiom_fmla = self._tran_sys.axiom_fmla
        if axiom_fmla != il.And():
            free_vars  = ilu.free_variables(axiom_fmla)
            if not il.is_forall(axiom_fmla) and len(free_vars) > 0:
                axiom_fmla = il.ForAll(free_vars, axiom_fmla)
            self.axiom_fmla = self.instantiate_quantifier(axiom_fmla)

    def _set_pretty_instantiations(self):
        self._set_dfs_variables()
        self._set_protocol_atoms()
        self._set_ivy_variables()
        self._set_ivy_actions()
        self._set_dependent_axioms()
        self._set_definitions()
        self._set_axioms()