# ------------------------------------
# --------- read trace code ----------
# Use AST functions to extract information from jit_trace.code
# Do some simplifications :
# -> Remove some useless getattr
# -> Decompose operations (a = f(g(b)))
# -> Remove TorchScript's operations (e.g. ops.prim.NumToTensor)
# The code given is an AST obj
# mostly "target = fct_name(*(const / var name / sub_obj))
# ------------------------------------

from .utils import *

# ==========================
# ====== B structure =======
# ==========================

class B_node():
    def __init__(self,target="",code=None,fct="",req=None,is_input=False):
        # "code" must be an AST, "fct" is a string
        self.target = target
        if code is None:
            code = make_ast_constant("/!\\ not defined /!\\")
        self.make_code(code)
        self.fct = fct
        if req is None:  self.req = set()
        else: self.req = req
        self.is_input = is_input
        self.is_rand = None # unknown for the moment
        self.req_rand = set()
        global all_nodes
        all_nodes.append(self)
    def make_code(self,code):
        if isinstance(code,ast.Assign):
            self.ast_code = code
        else:
            self.ast_code = ast.Assign([ast.Name(self.target)],code)
    def get_code(self):
        return ast_to_str(self.ast_code)

class B_var():
    def __init__(self,val,
            node : B_node = None,
            is_attr_of_self = False,
            path_from_self = None):
        # "val" must be an AST
        self.is_attr_of_self = is_attr_of_self
        self.path_from_self  = path_from_self
        self.val = val
        self.has_node = False # by default, e.g. has_node = False for const
        self.is_rand = False # by default
        if node is not None:
            if node.req==set() and not node.is_input:
                if node.fct in list_rand_fct:
                    dict_rand[node.target] = node.get_code()
                    self.is_rand = True
                else: # src neither input or rand
                    assert(isinstance(node.ast_code,ast.Assign))
                    self.val = node.ast_code.value
            else:
                self.has_node = True
                self.node = node

    def get_value(self,calling_node):
        if self.is_rand:
            calling_node.is_rand = True
            calling_node.req_rand.add(self.val)
        elif self.has_node:
            calling_node.req.add(self.node)
        return self.val

    def inherits(self,parent,l_attr): # for a getattr
        if parent.has_node:
            self.has_node = True
            self.node = parent.node
        self.path_from_self = parent.path_from_self + l_attr

class B_graph():
    def __init__(self):
        self.nodes  = [] # tmp -> should not be trusted
        self.output = None # B_var
        self.dict_rand = dict_rand # str code

# ==========================


# ==========================
# ====== Make B graph ======
# ==========================

dict_rand = {} # all random targets
all_nodes = [] # list of all the nodes generated
fresh_var = 0 # count the number of vars used over all the prgm

def open_sub_module(sub_mod,sub_mod_str,sub_fct,inputs_vars,is_main=False):
    # -> B_graph
    # ex : sub_mod     = jit_tr_GPT2.wpe 
    #      sub_mod_str = "self.wpe"
    #      sub_fct     = "forward"
    # inputs_vars : B_vars on which the sub_fct is applied
    if sub_fct=="forward": # quick fix
        code,memory = sub_mod.code_with_constants
    else:
        code,memory = getattr(sub_mod,sub_fct).code_with_constants
    if not isinstance(memory,dict): # quick fix, due to a type error in jit
        memory = memory.const_mapping
    a = (ast.parse(code)).body[0]

    dict_vars = {}
    dict_vars["self"] = B_var(
            val = ast.Name(sub_mod_str),
            is_attr_of_self=True,
            path_from_self=[])
    nodes = []

    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # -- Inputs --
    inputs = []
    for arg in a.args.args:
        inputs.append(arg.arg)
    nb_i = len(inputs)
    if is_main: # /!\
        for i in range(1,nb_i):
            i_node = B_node(
                target=inputs[i],
                code=make_ast_constant("INPUT"),
                fct="INPUT",
                req=set(),
                is_input=True)
            dict_vars[inputs[i]]=B_var(ast.Name(inputs[i]),node=i_node)
    else:
        assert(nb_i == len(inputs_vars)+1)
        for i in range(1,nb_i): #inputs[0]="self"
            dict_vars[inputs[i]]=inputs_vars[i-1]
            # Link local inputs' names with global vars
    # ~~~~~~~~~~~~~~~~~~~~~~~~~

    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # -> variables' names must be unique through all the program
    def make_unique(s):
        global fresh_var ; fresh_var += 1
        return f"__{fresh_var}_{s}"
    # -> In case we add new lines :
    def get_fresh_var():
        global fresh_var ; fresh_var += 1
        return f"__{fresh_var}_fv"
    # ~~~~~~~~~~~~~~~~~~~~~~~~~

    # ===== AUXILARY FUNCTIONS ===== 
    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # -- handle attribute -- 
    # -> explicit "getattr" or using "." (e.g. self.wpe)
    def aux_make_ast(p_val,format_fct,l_attr): # -> AST
        if isinstance(p_val,ast.Name):
            new_val = format_fct(p_val)
        else:
            attr = '.'.join(l_attr)
            new_val = ast.Call(
                func=ast.Name("getattr"),
                args=[p_val,make_ast_constant(attr)],
                keywords=[])
        return new_val

    def aux_handle_attr(target,parent_var,format_fct,l_attr):
        if parent_var.is_attr_of_self:
            p_val = parent_var.val
            new_val = aux_make_ast(p_val,format_fct,l_attr)
            new_var = B_var(new_val,is_attr_of_self=True)
            new_var.inherits(parent_var,l_attr)
        else:
            if target is None:
                new_id = get_fresh_var()
            else:
                new_id = make_unique(target)
            new_node = B_node(target=new_id,fct="getattr")
            p_val = parent_var.get_value(calling_node=new_node)
            new_val = aux_make_ast(p_val,format_fct,l_attr)
            new_node.make_code(new_val)
            new_var = B_var(new_val,node=new_node)
        return new_var

    def handle_attr(expr : ast.Attribute,target : str):
        l_name = open_attr_until_name(expr)
        assert(l_name[0] in dict_vars)
        # -> else raise "Unknown variable, global ?"
        parent_var = dict_vars[l_name[0]]
        attr = '.'.join(l_name[1:])
        format_fct = lambda pv : ast.Name(pv.id + "." + attr)
        return aux_handle_attr(target,parent_var,format_fct,l_name[1:])
    # ~~~~~~~~~~~~~~~~~~~~~~~~~

    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # -- open list of targets e.g. tuple --
    # -> so that each node has only one target
    # (e.g. l = ... ; a = l[0] ; b = l[1])
    def init_targets(list_tg):
        if len(list_tg)==1:
            return make_unique(list_tg[0])
        else:
            return get_fresh_var()

    def handle_targets(list_tg,main_var): # str list of len > 1
        for i,tg in enumerate(list_tg):
            new_tg_id  = make_unique(tg)
            new_node   = B_node(target=new_tg_id,fct="getattr")
            main_val   = main_var.get_value(calling_node=new_node)
            assert(isinstance(main_val,ast.Name))
            # else : to much simplifications :/ 
            new_node.make_code(
                ast.Subscript(main_val,make_ast_constant(i)))
            new_var    = B_var(ast.Name(new_tg_id),node=new_node)
            dict_vars[tg] = new_var
    # ~~~~~~~~~~~~~~~~~~~~~~~~~

    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # -- handle a function call -- (cross recursive with handle_expr)
    def handle_call(expr : ast.Call,target) -> B_var:
        l_name = open_attr_until_name(expr.func) # full name
        args = list(expr.args)

        # == explicit getattr ==
        if len(l_name)==1 and l_name[0]=='getattr':
            assert(len(args)==2)
            assert(is_constant(args[1]))
            # assert(isinstance(args[1],ast.Constant))
            # -> otherwise handle_expr ?
            parent_var = handle_expr(args[0])
            attr = args[1].value
            if attr.isdigit():
                format_fct = lambda pv : ast.Subscript(
                    value=pv,
                    slice=make_ast_constant(int(attr)))
            else:
                format_fct = lambda pv : ast.Call(
                    func=ast.Name("getattr"),
                    args=[pv,make_ast_constant(attr)],
                    keywords=[])
            return aux_handle_attr(target,parent_var,format_fct,[attr])
                # might create one node

        # == torchscript's functions == 
        # -> must be removed because some refer to TS global var
        elif l_name[0]=="ops":
            assert(len(args)==1)
            return handle_expr(args[0],target)
        elif l_name[0]=="int":
            return handle_expr(args[0],target)
        elif l_name[0]=="annotate":
            assert(len(args)==2)
            return handle_expr(args[1],target)

        else: # -> real function
            args_Bvar = [handle_expr(ar,target=None) for ar in args]
            # == sub module ==
            if l_name[0] in dict_vars:
                sub_var = dict_vars[l_name[0]]
                print_debug(f"In {sub_mod_str}.{sub_fct} try to sub "\
                            f"open {ast_to_str(sub_var.val)}.{l_name[1:]}")
                assert(sub_var.is_attr_of_self)
                sub_sub_mod = sub_mod
                path_from_self = sub_var.path_from_self + l_name[1:-1]
                for at in path_from_self:
                    sub_sub_mod = getattr(sub_sub_mod,at)
                sub_sub_str = ast_to_str(sub_var.val)
                sub_graph = open_sub_module(
                        sub_sub_mod,sub_sub_str,l_name[-1],args_Bvar)
                return sub_graph.output # which is a B_var !

            # == builtin functions ==
            else:
                if target is None:
                    target = get_fresh_var()

                # == torch.nn.functional / torch.Tensor == quick.fix
                if l_name[0]=="torch" and len(l_name)==2:
                    try: exec(f"torch.{l_name[1]}")
                    except:
                      try: exec(f"torch.nn.functional.{l_name[1]}")
                      except:
                        try: exec(f"torch.Tensor.{l_name[1]}")
                        except:
                          raise Exception(
                            f"torch.{l_name[1]} neither found in torch, "\
                            f"torch.Tensor and torch.nn.functional")
                        else: fct_name = f"torch.Tensor.{l_name[1]}"
                      else: fct_name = f"torch.nn.functional.{l_name[1]}"
                    else: fct_name = f"torch.{l_name[1]}"
                else:
                    fct_name = ".".join(l_name)

                # == else ==
                new_node = B_node(target=target,fct=fct_name)
                args_ast = [
                    v.get_value(calling_node=new_node)
                    for v in args_Bvar]
                kwds_ast = []
                for kw in expr.keywords:
                    if var_impose_device and kw.arg=="device":
                        kwds_ast.append(
                          ast.keyword("device",ast.Name("device")))
                    elif not (((kw.arg=="dtype" or kw.arg=="layout")
                        and is_constant(kw.value)
                        and isinstance(kw.value.value,int))
                        or (kw.arg=="layout" and kw.value.value is None)):
                        kwds_ast.append(
                          ast.keyword(
                            kw.arg,
                            (handle_expr(kw.value)).get_value(new_node)))
                new_node.make_code(ast.Call(
                    func=ast.Name(fct_name),
                    args=args_ast,
                    keywords=kwds_ast))
                return B_var(ast.Name(target),node = new_node)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~

    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # isinstance(expr, ast.List or ast.Tuple)
    # constr = "list" or "tuple"
    # in this version, I'm *not* inserting them here, I will do it later. 
    # -> because I need to precise the calling_node...
    def aux_handle_tuple_or_list(expr,target,constr):
        if target is None: target = get_fresh_var()
        new_node = B_node(target=target,fct=f"{constr} constructor")
        args_vars = [handle_expr(v) for v in expr.elts]
        args_ast  = [v.get_value(calling_node=new_node) for v in args_vars]
        if constr=="list": c = ast.List(args_ast)
        else: c = ast.Tuple(args_ast)
        new_node.make_code(c)
        return B_var(ast.Name(target),node=new_node)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~

    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # -- handle any expr -- return type -> B_var
    # -> the main recursive fct to handle ast
    # if the expr is simple (e.g. constant or self's attr) 
    # -> B_var.has_node == False
    # otherwise, a node (= a piece of code) is created. 
    # The optional parameter  "target" imposes the name of the var created
    # /!\ TorchScript's global constant vars must have been removed
    def handle_expr(expr,target : str = None) -> B_var :
        if is_constant(expr):
            return B_var(expr)
        elif isinstance(expr,ast.Name):
            assert(expr.id in dict_vars)
            return dict_vars[expr.id]
        elif (  isinstance(expr,ast.Attribute) # -> special constants
            and isinstance(expr.value,ast.Name)
            and expr.value.id == 'CONSTANTS' ):
            return B_var(make_ast_constant(memory[expr.attr]))
        elif isinstance(expr,ast.Attribute):
            return handle_attr(expr,target) # may creates one node
        elif isinstance(expr,ast.Call):
            return handle_call(expr,target)
            # may creates nodes for arguments (+ for output=target)
        elif isinstance(expr,ast.List):
            return aux_handle_tuple_or_list(expr,target,"list")
        elif isinstance(expr,ast.Tuple):
            return aux_handle_tuple_or_list(expr,target,"tuple")
        elif isinstance(expr,ast.UnaryOp):
            assert(isinstance(expr.op,ast.USub)) # quick fix
            assert(is_constant(expr.operand))
            return B_var(expr)
        else:
            raise Exception(f"{type(expr)} unknown")

    # ~~~~~~~~~~~~~~~~~~~~~~~~~
    # =========================

    # == MAIN ==
    for n in a.body:
        if isinstance(n,ast.Assign):
            # -- targets -- 
            list_tg = [] ; tg = n.targets[0]
            if isinstance(tg,ast.Name):
                list_tg = [tg.id]
                target_id = tg.id
            elif isinstance(tg,ast.Tuple) or isinstance(tg,ast.List):
                for e in tg.elts: list_tg.append(e.id)
                target_id = None
            else:
                raise Exception(
                    f"ast.Call's target neither name, tuple or list ?"
                    f"{type(tg)} found")

            # -- main --
            main_id  = init_targets(list_tg)
            main_var = handle_expr(n.value,main_id)
            if len(list_tg)>1:
                handle_targets(list_tg,main_var)

            if target_id is not None:
                dict_vars[target_id] = main_var

        else:
            assert(isinstance(n,ast.Return))
            ret_graph = B_graph()
            ret_graph.output = handle_expr(n.value,target=None)
            return ret_graph

    raise Exception("No ast.Return found at the end of jit.code ??!")

# ==========================

# ===== Main function ======

def make_B(nn_mod,ex_inputs,verbose=None,impose_device=True):
    # main_mod must be a instance of torch.nn.Module
    # ex_inputs can be either a tuple or a dict
    # -- global vars --
    global fresh_var, var_impose_device, dict_rand, all_nodes
    all_nodes = [] ; dict_rand = {} ; fresh_var = 0
    var_impose_device = impose_device
    if not (verbose is None): ref_verbose[0] = verbose

    # -- ex_inputs --
    if isinstance(ex_inputs,dict):
        ex_inputs = tuple(ex_inputs.values())

    main_mod = torch.jit.trace_module(
            nn_mod, {'forward': ex_inputs}, check_trace=False)
    main_str = "self"
    main_fct = "forward"
    main_g = open_sub_module(main_mod,main_str,main_fct,[],is_main=True)
    main_g.nodes = all_nodes
    all_nodes = []
    return main_g
