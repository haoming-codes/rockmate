# from rockmate.utils import (
#     print_debug,
#     get_device,
#     pgb,
#     np,
#     ast_to_str,
# )
import pgb
from pgb.utils import print_debug, np
from pgb.utils.global_vars import ref_verbose
from pgb.utils.small_fcts import get_device
from pgb.utils.ast_add_on import ast_to_str
from rockmate.def_code import RK_Storage, DelOp
from rockmate.def_chain import RK_Chain
from rockmate.def_sequence import (
    SeqBlockBwd,
    SeqBlockFc,
    SeqBlockFn,
    SeqBlockFe,
)
from rockmate.rotor_solver import seq_builder
from rockmate.translator import Translator, RngState
import torch


def print_memsizes(list_kg):
    di = list_kg[-1].dict_info
    for kg in list_kg:
        for n in kg.dict_nodes.values():
            mt = n.main_target
            try:
                print_debug(
                    f"{mt} : memsize {di[mt].memsize} ; " f"fm {n.fgt_mem.v}",
                    end="",
                )
            except:
                print_debug("\nloss")
    print_debug("\n")


class CheckpointedModule(torch.nn.Module):
    def __init__(
        self,
        original_mod,
        dict_inputs,
        mem_limit=None,
        mem_unit=None,
        verbose=False,
        get_chain=True,
        get_sequence=True,
        get_code=True,
        nb_budget_abar=5,
        nb_budget_all=5,
    ):
        super(CheckpointedModule, self).__init__()
        ref_verbose[0] = verbose
        self.device = get_device()
        self.original_mod = original_mod
        # for k,v in original_mod.state_dict().items():
        #     self.register_parameter(k,v)
        self.mem_unit = mem_unit if mem_unit else 1024 ** 2
        # -- use pytorch graph builder to get the list of K_graphs --
        self.pgb_res = pgb.make_all_graphs(
            original_mod, dict_inputs, verbose=verbose, bool_kg=False
        )  # we don't need the whole K_graph
        self.list_kg = self.pgb_res.K_graph_list
        self.init_code = ast_to_str(self.list_kg[0].init_code)
        self.output = self.list_kg[-1].output_kdn_data
        if get_chain:
            self.get_chain(nb_budget_abar, nb_budget_all)
        if get_sequence:
            self.get_sequence(mem_limit)
        if get_code:
            self.get_code()

    def get_chain(self, nb_budget_abar=10, nb_budget_all=5):
        #  -- use checkmate to solve all the blocks --
        self.rk_chain = RK_Chain(
            self.list_kg,
            self.pgb_res.equivalent_classes,
            nb_budget_abar,
            nb_budget_all,
            mem_unit=self.mem_unit,
        )

    def get_sequence(self, mem_limit):
        for n, p in self.original_mod.named_parameters():
            if p.grad is None:
                p.grad = torch.zeros_like(p)

        self.peak_overhead = max(
            [
                sum(
                    [
                        kdn.mem.v
                        for kdn in kcn.deps_fake
                        if kdn.main_target != kcn.main_target
                    ]
                )
                for kg in self.list_kg
                for kcn in kg.list_kcn
            ]
        )

        if mem_limit:
            self.mem_limit = mem_limit
        else:
            self.mem_limit = (
                # If not given a budget, we use the current available memory
                torch.cuda.get_device_properties(0).total_memory * 0.9
                - torch.cuda.memory_allocated()
                - self.peak_overhead
            )
        print_debug("mem_limit", self.mem_limit)
        # -- solve the chain like rotor --
        self.seq = seq_builder(self.rk_chain, self.mem_limit // self.mem_unit)
        enable = np.zeros(len(self.list_kg))
        for s in self.seq.seq:
            if isinstance(s, SeqBlockFe):
                enable[s.index] = 1
            if isinstance(s, SeqBlockBwd):
                if not enable[s.index - 1]:
                    s.op_sched.del_input()
                    # s.op_sched.op_list.append(s.op_sched.del_input_op)
                    # s.op_sched.alive_list.append(s.op_sched.alive_list[-1])
                    # s.op_sched.alive_list[-1][-1] = 0
                    # s.op_sched.save = np.append(
                    #     s.op_sched.save, s.op_sched.save[-1]
                    # )

        self.fwd_seq, self.bwd_seq = self.seq.cut_fwd_bwd()
        self.fwd_op_list = [
            op for seq in self.fwd_seq.seq for op in seq.op_sched.op_list
        ]
        self.bwd_op_list = [
            op for seq in self.bwd_seq.seq for op in seq.op_sched.op_list
        ]

    def get_code(self, aggressive=True):
        self.storage = RK_Storage(self.device, self.original_mod)
        self.storage.gd["rng_state"] = RngState()
        self.translator = Translator(self.storage, aggressive=aggressive)
        fwd_code = []
        for seq_block in self.fwd_seq.seq:
            fwd_code.append(self.translator.translate(seq_block.op_sched, True))
        bwd_code = []
        for seq_block in self.bwd_seq.seq:
            bwd_code.append(
                self.translator.translate(seq_block.op_sched, False)
            )
        self.fwd_code = fwd_code
        self.bwd_code = bwd_code
        self.full_code = []
        for code_list in fwd_code + bwd_code:
            self.full_code += code_list

    def _exec(self, code_list, record_mem=False):
        for code in code_list:
            if record_mem:
                torch.cuda.reset_peak_memory_stats()
                self.mem_before = torch.cuda.memory_allocated()
                self.max_before = torch.cuda.max_memory_allocated()
            try:
                exec(code, self.storage.gd, self.storage.ld)
            except Exception as e:
                print(f"Failed to execute code:\n {code}")
                raise (e)
                break
            if record_mem:
                allo_mem = torch.cuda.memory_allocated() - self.mem_before
                peak_mem = torch.cuda.max_memory_allocated() - self.max_before
                self.max_mem.append(peak_mem - allo_mem)
                self.allo_mem.append(allo_mem)

    def forward(self, input, record_mem=False):
        self.storage.add_val("src", input)  #  hardcoded
        exec(self.init_code, self.storage.gd, self.storage.ld)

        self.max_mem = []
        self.allo_mem = []
        for code_list, seq in zip(self.fwd_code, self.fwd_seq.seq):
            if seq.op_sched.no_grad:
                with torch.no_grad():
                    self._exec(code_list, record_mem)
            else:
                with torch.enable_grad():
                    self._exec(code_list, record_mem)

        return self.storage.get_val(self.output.main_target)

    def backward(self, record_mem=False, add_output_grad=True):
        if record_mem:
            # self.allo_mem[-1] += self.output.info.memsize.v
            # output grad is generated outside
            loss_idx = len(self.allo_mem)
        for code_list, seq in zip(self.bwd_code, self.bwd_seq.seq):
            if seq.op_sched.no_grad:
                with torch.no_grad():
                    self._exec(code_list, record_mem)
            else:
                with torch.enable_grad():
                    self._exec(code_list, record_mem)
        if record_mem and add_output_grad:
            self.allo_mem[loss_idx] += self.output.info.memsize.v

    def expect_time(self):
        # Sum of the measured time of each operation for one batch
        return self.fwd_seq.compute_time() + self.bwd_seq.compute_time()

    def expect_mem(self, overhead=False):
        # Peak mem based on the measured memory/overhead of each operation
        pred_mem = []
        acc_mem = np.zeros(len(self.fwd_seq.seq))
        # alive_dict = {}
        # for kg in self.list_kg:
        #     for kdn in kg.list_kdn:
        #         alive_dict[kdn.name] = (0, kdn.mem)
        for i, seq in enumerate(self.fwd_seq.seq + self.bwd_seq.seq):
            op_sched = seq.op_sched
            for a, op in zip(op_sched.alive_list, op_sched.op_list):
                acc_mem[seq.index] = (
                    np.dot(a, op_sched.mem_sizes) - op_sched.input_size[1]
                )
                # if not op_sched.is_fwd:
                #     acc_mem[seq.index] -= op_sched.output_size[1]
                pred_mem.append(sum(acc_mem))
                if overhead and op.op_type == "Run":
                    pred_mem[-1] += op.overhead
            # for s, op in zip(op_sched.save, op_sched.op_list):
            # for i, op in enumerate(op_sched.op_list):
            # acc_mem[seq.index] = s
            # pred_mem.append(sum(acc_mem))
            # if overhead and op.op_type == "Run":
            #     pred_mem[-1] += op.overhead
        return pred_mem

    def reinit(self):
        self.original_mod.zero_grad()
        self.storage.ld = {}
