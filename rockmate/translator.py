from pgb.utils.ast_add_on import make_str_assign, make_str_list_assign
from pgb.utils import np, torch
from rockmate.def_code import DelOp


class RngState:
    def __init__(self):
        self.cpu_states = {}
        self.gpu_states = {}

    def get(self, op_name):
        if op_name not in self.cpu_states.keys():
            self.cpu_states[op_name] = torch.get_rng_state()
            self.gpu_states[op_name] = torch.cuda.get_rng_state()

    def restore(self, op_name):
        # pass
        torch.set_rng_state(self.cpu_states[op_name])
        torch.cuda.set_rng_state(self.gpu_states[op_name])


class Translator:  # to execute Op
    def __init__(self, storage, aggressive=True):
        self.storage = storage
        self.live = {}
        self.fgt = []
        self.code = []
        self.grad = {}
        self.fwd_op_sched = []
        self.bwd_op_sched = []
        self.op_info = []
        self.fwd_code = []
        self.aggressive = aggressive
        if self.aggressive:
            self.alive_global = {}
            self.info_global = {}

    def _estimate_memory(self):
        mem = 0
        for k, v in self.live.items():
            mt, data = k.split(".")
            if v:
                mem += self.mt2op[mt].mem
        return mem

    def translate(self, op_sched, during_fwd=True, reorder=False):
        if self.aggressive:
            for i, kdn_name in enumerate(op_sched.kdn_names):
                self.alive_global[kdn_name] = op_sched.alive_list[-1][i]
                self.info_global[kdn_name] = op_sched.kdn_info[kdn_name]
        else:
            self.alive_global = {}
        # Fc/Fn cases
        if op_sched.no_grad:
            code_list = []  # ["with torch.no_grad():"]
            for i, op in enumerate(op_sched.op_list):
                if op.op_type == "Run":
                    if "loss" in op.main_target:
                        code_list.append("")
                    else:
                        # code = ast_to_str(make_ast_module([op.main_code]))
                        # code += "\n"+ast_to_str(make_ast_module(op.body_code))
                        # code = op.code
                        # code = "\t".join(code.splitlines(True))
                        if op.is_rand:
                            code = f"rng_state.get('{op.name}');rng_state.restore('{op.name}')\n{op.code}"
                        else:
                            code = op.code
                        code_list.append(f"{code}")
                elif op.kdn_type == "data":
                    code = ""
                    if op_sched.del_input_idx == i:
                        for target in op_sched.del_input_op.tensor_targets:
                            # code += f"del {target};"
                            code += (
                                f"{target}.data = torch.zeros(0,device=device);"
                            )
                    else:
                        for target in op.all_targets:
                            code += f"del {target};"
                    code_list.append(code)
                else:
                    code_list.append("")
                # if op_sched.del_input_idx == i:
                #     code = "\n"
                #     for target in op_sched.del_input_op.tensor_targets:
                #         code += f"{target}.data = torch.zeros(0,device=device);"
                #     code_list[-1] += code
            code_list[-1] += f"\n{op_sched.output_size[0]}.requires_grad_()"
            return code_list

        def _is_alive(kdn_name, i):
            if kdn_name in op_sched.kdn_names:
                return op_sched.alive_list[i][
                    op_sched.kdn_names.index(kdn_name)
                ]
            elif kdn_name in self.alive_global:
                return self.alive_global[kdn_name]
            else:
                return True

        def _generate_fake_data(kdn, i, is_self=False):
            # return code for generate the target fake tensor (only for data/grad)
            prep_code = ""
            after_code = ""
            req_shape = kdn.info.tsize
            target_tensor = None
            mt = kdn.main_target
            dict_info = (
                self.info_global if self.aggressive else op_sched.kdn_info
            )
            for name, info in dict_info.items():
                if "data" not in name or info is None:
                    continue
                if np.prod(info.tsize) == np.prod(req_shape) and _is_alive(
                    name, i
                ):
                    target_tensor = name.split(" ")[0]  # main_target
            if is_self:
                target_tensor = f"{kdn.main_target}.grad"
            if (target_tensor is None) or not self.aggressive:
                # No available live tensor to use
                target_tensor = f"torch.zeros({req_shape},device=device)"
                prep_code += f"{mt}.data = {target_tensor};"
            else:
                prep_code += (
                    f"{mt}.data = {target_tensor}.reshape({req_shape});"
                )
            # prep_code += (
            #     ";".join(
            #         [make_str_assign(bc) for bc in list(kdn.deps)[0].body_code]
            #     )
            #     + "\n"
            # )
            # after_code += f"{mt}.data = torch.zeros(0,device=device);"
            for v in kdn.all_targets:
                after_code += f"{v}.data = torch.zeros(0,device=device); "
            if is_self:
                prep_code += f"_{mt}.data = {target_tensor};"
                after_code += f"_{mt}.data = torch.zeros(0,device=device);"
            return prep_code, after_code

        def _run_op(op, i):
            # Forward operation
            mt = op.main_target
            if "fwd" in op.name:
                rec = (i > op_sched.op_list.index(op)) or (not op_sched.is_fwd)
                code = make_str_assign(op.main_code) + "\n"
                if op.proxy:
                    if (
                        (not during_fwd)
                        and (not op_sched.no_grad)
                        and (mt == op_sched.output_size[0])
                    ):
                        rec = True
                    # code = make_str_assign(op.main_code, prefix="_") + ";"
                    # if not rec:
                    #     code += f"{mt} = _{mt};\n"

                # else:
                #     code = make_str_assign(op.main_code) + "\n"
                code += make_str_list_assign(op.inplace_code) + "\n"
                if op.proxy:
                    for target in op.tensor_targets:
                        code = code.replace(target, "_" + target)
                    if rec:
                        code += f"{mt}.data = _{mt}.data;"
                    else:
                        code += f"{mt} = _{mt}.detach().requires_grad_();"
                for bc in op.body_code:
                    suffix = ""
                    if rec and (bc[0] in op.tensor_targets):
                        suffix = ".data"
                    code += "\n" + make_str_assign(bc, suffix=suffix)

                if op.is_rand:
                    code = f"rng_state.get('{op.name}');rng_state.restore('{op.name}')\n{code}"
                return code
            # Backward operation
            elif "bwd" in op.name:
                mt = op.main_target
                rec = op in op_sched.op_list[:i]
                last = not (op in op_sched.op_list[i + 1 :])
                prep_code = ""
                after_code = ""
                for kdn in op.deps_fake:
                    if (
                        not _is_alive(kdn.name, i)
                        # or op_sched.input_size[0] in kdn.name
                    ):
                        fake_code = _generate_fake_data(
                            kdn, i, is_self=(kdn.main_target == op.main_target)
                        )
                        prep_code += fake_code[0]
                        after_code += fake_code[1]
                if rec:
                    prev_i = i - op_sched.op_list[:i][::-1].index(op) - 1
                    rec_list = []
                    for kdn in op.users_global:
                        if DelOp(kdn) in op_sched.op_list[prev_i:i]:
                            rec_list += kdn.all_targets
                    inputs = ",".join(rec_list)
                    code = f"_{mt}.backward({mt}.grad, inputs=[{inputs}], retain_graph={not last})"
                else:
                    code = f"_{mt}.backward({mt}.grad, retain_graph={not last})"
                bwd_code = f"{prep_code}\n" f"{code}\n" f"{after_code}"
                if op.is_rand:
                    bwd_code = f"rng_state.get('{op.name}');rng_state.restore('{op.name}')\n{bwd_code}"
                return bwd_code

        def _del_op(op, i):
            code = ""
            if op.kdn_type == "data":
                if (
                    op.info is not None
                    and op.info.requires_grad
                    and _is_alive(op.name.replace("data", "phantoms"), i)
                    and op.proxy
                ):
                    code += f"_{op.main_target}.data = torch.zeros(0,device=device);"
                for v in op.tensor_targets:
                    code += f"{v}.data = torch.zeros(0,device=device); "
            if op.kdn_type == "grad":
                code += f"{op.main_target}.grad = None"
            if op.kdn_type == "phantoms":
                code += f"del _{op.main_target}"
            return code

        code_list = []
        for i, (op, alive) in enumerate(
            zip(op_sched.op_list, op_sched.alive_list)
        ):
            if op.op_type == "Run":
                code_list.append(_run_op(op, i))
            if op.op_type == "Del":
                code_list.append(_del_op(op, i))
            # if op_sched.del_input_idx == i:
            #     code = "\n"
            #     for target in op_sched.del_input_op.tensor_targets:
            #         code += f"{target}.data = torch.zeros(0,device=device);"
            #     code_list[-1] += code
        return code_list
