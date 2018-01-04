import sys, os, ast
import pg_logger
from collections import defaultdict
from queue import Queue

class VarEnvironment():
    def __init__(self, execution_point):
        self.heap = execution_point['heap']
        self.globals = execution_point['globals']
        if len(execution_point['stack_to_render']) > 0:
            frame = execution_point['stack_to_render'][-1]
            self.locals = frame['encoded_locals']
            self.frame_hash = frame['unique_hash']
        else:
            self.locals = {}

    def get_var(self, name):
        if name in self.locals:
            return self.frame_hash + ':' + name
        elif name in self.globals:
            return 'global:' + name
        else:
            return 'undefined:' + name

    def get_ref(self, name):
        if name in self.locals:
            [tag, ref] = self.locals[name]
            assert(tag == 'REF') # Ref primitives
            return ref
        elif name in self.globals:
            [tag, ref] = self.globals[name]
            assert(tag == 'REF') # Ref primitives
            return ref
        else:
            return None

    def vars(self):
        my_vars = self.heap.copy()
        
        for name in self.globals:
            my_vars['global:' + name] = self.globals[name]

        for name in self.locals:
            my_vars[self.frame_hash + ':' + name] = self.locals[name]

        return my_vars
        
    # Gets changes made in the second
    # Returns set of locations
    def diff(self, other):
        my_vars = self.vars()
        their_vars = other.vars()

        # New variables
        changes = their_vars.keys() - my_vars.keys()

        # Old variables that changed
        for loc in my_vars:
            if loc in their_vars and my_vars[loc] != their_vars[loc]:
                changes.add(loc)

        return changes

# Attempts to find an attribute for a value and identifier
# Returns a heap ref and a value
def find_attribute(var_env, val, identifier):
    if val[0] != 'INSTANCE':
        return None, None
    
    attr_pairs = val[2:]
    for ref1, ref2 in attr_pairs:
        assert(ref1[0] == 'REF')
        assert(ref2[0] == 'REF')
        key = var_env.heap[ref1[1]]
        assert(key[0] == 'HEAP_PRIMITIVE')
        assert(key[1] == 'str')
        if key[2] == identifier:
            value = var_env.heap[ref1[1]]
            return ref1[1], value

# Attempts to find heap locations for the expression
# Returns a set of heap refs, plus a value for the overall expression
def find_refs(var_env, expr):
    if isinstance(expr, ast.Name):
        name_ref = var_env.get_ref(expr.id)
        return set([name_ref]), var_env.heap[name_ref]
    elif isinstance(expr, ast.Attribute):
        instance_refs, instance = find_refs(var_env, expr.value)

        if not instance:
            return instance_refs, None
        
        attr_ref, attr_value = find_attribute(var_env, instance, expr.attr)
        if attr_ref:
            instance_refs.add(attr_ref)
            return instance_refs, attr_value
        else:
            return instance_refs, None
    else:
        raise 'Unsupported find_refs argument'
    
ignored_events = set(['raw_input'])
def trace(source, ri):
    def finalizer(input_code, output_trace):
        filtered_trace = [ep for ep in output_trace if ep['event'] not in ignored_events]
        return filtered_trace
    
    return pg_logger.exec_script_str_local(source,
                                           ri,
                                           True,
                                           True,
                                           finalizer)

class LineMapVisitor(ast.NodeVisitor):
    def __init__(self):
        self.the_map = {}

    def visit(self, node):
        if isinstance(node, ast.stmt):
            self.the_map[node.lineno] = node
        self.generic_visit(node)

"""
Currently only handles the case where statements are on separate lines.

TODO: Transform the original source so that statements are always on separate
lines.
"""
def make_line_maps(source):
    astree = ast.parse(source)
    map_visitor = LineMapVisitor()
    map_visitor.visit(astree)

    control_visitor = ControlVisitor()
    control_visitor.visit(astree)
    
    return map_visitor.the_map, control_visitor.line_to_controller

class UseVisitor(ast.NodeVisitor):
    def __init__(self, exec_point):
        self.exec_point = exec_point
        self.env = VarEnvironment(exec_point)
        self.use_set = set()

    def die(self, node):
        raise ValueError('Unsupported node: ' + str(type(node)))

    def nothing(self, node):
        pass

    # Exprs
    # BoolOp
    # BinOp
    # UnaryOp
    visit_Lambda = die
    # IfExp
    # Dict
    # Set
    visit_ListComp = die
    visit_SetComp = die
    visit_DictComp = die
    visit_GeneratorExp = die
    visit_Await = die
    visit_Yield = die
    visit_YieldFrom = die
    # Compare
    # Call
    # Num
    # Str
    # FormattedValue
    # JoinedStr
    # Bytes
    # NameConstant
    visit_Ellipsis = die
    # Constant
    
    def visit_Attribute(self, node):
        refs, _ = find_refs(self.env, node)
        self.use_set |= refs
    
    visit_Subscript = die
    
    visit_Starred = die

    def visit_Name(self, node):
        self.use_set.add(self.env.get_var(node.id))
        self.use_set.add(self.env.get_ref(node.id))

    # List
    # Tuple

    # Stmts

    # TODO: Is this correct? Need to account for captured vars?
    visit_FunctionDef = nothing
    
    visit_AsyncFunctionDef = die
    visit_ClassDef = visit_FunctionDef # TODO: account for class-specific stuff
    # Return
    
    visit_Delete = die

    def visit_Assign(self, stmt):
        self.visit(stmt.value)

    def visit_AugAssign(self, stmt):
        self.visit(stmt.target)
        self.visit(stmt.value)

    visit_AnnAssign = visit_Assign

    def visit_For(self, stmt):
        # Ignore target
        self.visit(stmt.iter)
        
    visit_AsyncFor = die

    def visit_IfLike(self, stmt):
        self.visit(stmt.test)

    visit_While = visit_IfLike
    visit_If = visit_IfLike
        
    visit_With = die
    visit_AsyncWith = die
    # Raise
    visit_Try = die
    # Assert
    visit_Import = nothing
    visit_ImportFrom = nothing
    visit_Global = nothing
    visit_Nonlocal = nothing
    # Expr
    # Pass
    # Break
    # Continue

# Creates a map from statement lines to the lines of the immediately-enclosing
# controller.
#
# TODO: Support unstructured control flow (break, continue, early return, etc.)
class ControlVisitor(ast.NodeVisitor):
    def __init__(self):
        # Maps statements to their immediate controllers
        self.line_to_controller = {}
        self.enclosing_controller = 0

    def die(self, node):
        raise ValueError('Unsupported node: ' + str(type(node)))

    def nothing(self, node):
        pass

    def visit(self, node):
        if isinstance(node, ast.stmt):
            self.line_to_controller[node.lineno] = self.enclosing_controller

        super(ControlVisitor, self).visit(node)
    
    def enclosed_visit(self, lineno, node):
        old_encloser = self.enclosing_controller

        self.enclosing_controller = lineno
        self.visit(node)
        self.enclosing_controller = old_encloser

    def enclosed_visits(self, lineno, nodes):
        old_encloser = self.enclosing_controller

        self.enclosing_controller = lineno
        
        for node in nodes:
            self.visit(node)
        
        self.enclosing_controller = old_encloser

    def visit_FunctionDef(self, stmt):
        self.enclosed_visits(stmt.lineno, stmt.body)

    visit_AsyncFunctionDef = die
    visit_ClassDef = visit_FunctionDef # TODO: handle class-specific things?

    # Return - TODO: Support early return

    def visit_IfLike(self, stmt):
        self.enclosed_visits(stmt.lineno, stmt.body)
        self.enclosed_visits(stmt.lineno, stmt.orelse)
    
    # TODO: {While, For} technically controls itself after the first iteration,
    # because it only executes if it didn't stop on the previous iteration
    visit_For = visit_IfLike
    visit_AsyncFor = die
    visit_While = visit_IfLike
    visit_If = visit_IfLike
    
    visit_With = die
    visit_AsyncWith = die

    visit_Raise = die
    visit_Try = die

    visit_Break = die
    visit_Continue = die
    
def used_stmt(exec_point, stmt):
    visitor = UseVisitor(exec_point)
    visitor.visit(stmt)
    return visitor.use_set

def defined_stmt(exec_point, next_exec_point):
    now_vars = VarEnvironment(exec_point)
    next_vars = VarEnvironment(next_exec_point)

    return now_vars.diff(next_vars)

# Returns a map from steps to lines and a combined UD and CT "multimap"
def build_relations(line_map, line_to_control, tr):
    # UD instead of DU, so we can go use -> definition. Similarly, use CT
    # instead of TC
    UD_CT = defaultdict(set)
    step_to_line = {}
    line_to_step = defaultdict(set)

    # Reference to step
    last_definitions = {}

    for step, exec_point in enumerate(tr):
        if exec_point['event'] not in ['step_line', 'exception', 'uncaught_exception']:
            continue

        line = exec_point['line']
        line_to_step[line].add(step)
        stmt = line_map[line]

        step_to_line[step] = line

        # Use-Definition processing
        stmt_useds = used_stmt(exec_point,  stmt)
        for ref in stmt_useds:
            if ref in last_definitions:
                UD_CT[step].add(last_definitions[ref])

        if exec_point['event'] == 'step_line':
            stmt_defineds = defined_stmt(tr[step], tr[step + 1])
            for ref in stmt_defineds:
                last_definitions[ref] = step

        # Test-Control processing
        # TODO: Support control dependencies caused by function calls
        control = line_to_control[line]
        if line_to_step[control]:
            control_step = max(line_to_step[control])
            UD_CT[step].add(control_step)

    return step_to_line, line_to_step, UD_CT

def find_exception(trace):
    for step, exec_point in enumerate(trace):
        if exec_point['event'] in ['uncaught_exception', 'exception']:
            return step

"""
Returns a set of line numbers.

TODO: Guess or allow specification of specific values to track.
"""

def slice(source, ri, line=None, debug=False):
    line_map, line_to_control = make_line_maps(source)
    tr = trace(source, ri)
    
    step_to_line, line_to_step, UD_CT = build_relations(line_map, line_to_control, tr)

    visited = set()
    queue = Queue()
    exception_step = find_exception(tr)

    if exception_step:
        print('Exception at line ' + str(step_to_line[exception_step]))
    elif not line:
        return None, 0

    for step in [exception_step] if exception_step else line_to_step[line]:
        queue.put(step)
    while not queue.empty():
        step = queue.get()
        if step in visited:
            continue
        visited.add(step)

        # Put influencing steps in the queue
        for infl_step in UD_CT[step]:
            queue.put(infl_step)

    keep_these = set([step_to_line[step] for step in visited])
    stmt_count = float(len(line_map))
            
    return keep_these, len(set(line_map) - keep_these) / stmt_count
