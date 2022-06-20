#
# created by Severin Dicks (IBSM, Freiburg)
#
#

import cupy as cp
import cupyx as cpx
import anndata

import numpy as np
import pandas as pd
import scipy
import math
from scipy import sparse
from typing import Any, Union, Optional, Mapping

import warnings

from scipy.sparse import issparse as issparse_cpu
from cupyx.scipy.sparse import issparse as issparse_gpu

from cuml.linear_model import LinearRegression



class cunnData:
    """
    The cunnData objects can be used as an AnnData replacement for the inital preprocessing of single cell Datasets. It replaces some of the most common preprocessing steps within scanpy for annData objects.
    It can be initalized with a preexisting annData object or with a countmatrix and seperate Dataframes for var and obs. Index of var will be used as gene_names. Initalization with an AnnData object is advised.
    """
    uns = {}
    def __init__(
        self,
        X: Optional[Union[np.ndarray,sparse.spmatrix, cp.array, cp.sparse.csr_matrix]] = None,
        obs: Optional[pd.DataFrame] = None,
        var: Optional[pd.DataFrame] = None,
        uns: Optional[Mapping[str, Any]] = None,
        adata: Optional[anndata.AnnData] = None):
            if adata:
                if not issparse_cpu(adata.X):
                    inter = scipy.sparse.csr_matrix(adata.X)
                    self.X = cp.sparse.csr_matrix(inter, dtype=cp.float32)
                    del inter
                else:
                    self.X = cp.sparse.csr_matrix(adata.X, dtype=cp.float32)
                self.obs = adata.obs.copy()
                self.var = adata.var.copy()
                self.uns = adata.uns.copy()
                
            else:
                if issparse_gpu(X):
                    self.X = X                
                elif not issparse_cpu(X):
                    inter = scipy.sparse.csr_matrix(X)
                    self.X = cp.sparse.csr_matrix(inter, dtype=cp.float32)
                    del inter
                else:
                    self.X = cp.sparse.csr_matrix(X, dtype=cp.float32)

                self.obs = obs
                self.var = var
                self.uns = uns
    
    @property
    def shape(self):
        return self.X.shape
    @property
    def nnz(self):
        return self.X.nnz
    
    def __getitem__(self, index):
        """
        Currently only works for `obs`
        """
        index = index.to_numpy()
        return(cunnData(X = self.X[index,:],obs = self.obs.loc[index,:],var = self.var,uns=self.uns))
        

    def to_AnnData(self):
        """
        Takes the cunnData object and creates an AnnData object
        
        Returns
        -------
            annData object
        
        """
        adata = anndata.AnnData(self.X.get())
        adata.obs = self.obs.copy()
        adata.var = self.var.copy()
        adata.uns = self.uns.copy()
        return adata
    
    def calc_gene_qc(self, batchsize = None):
        """
        Filters out genes that expressed in less than a specified number of cells

        Parameters
        ----------
        
            batchsize: int (default: None)
                Number of rows to be processed together This can be adjusted for performance to trade-off memory use.
            
        Returns
        -------
            updated `.var` with `n_cells` and `n_counts`
            filtered cunndata object inplace for genes less than the threshhold
        
        """
        if batchsize:
            pass
            n_batches = math.ceil(self.X.shape[0] / batchsize)
            n_counts = cp.zeros(shape=(n_batches,self.X.shape[1]))
            n_cells= cp.zeros(shape=(n_batches,self.X.shape[1]))
            for batch in range(n_batches):
                start_idx = batch * batchsize
                stop_idx = min(batch * batchsize + batchsize, self.X.shape[0])
                arr_batch = self.X[start_idx:stop_idx]
                arr_batch = arr_batch.tocsc()
                n_cells_batch = cp.diff(arr_batch.indptr).ravel()
                n_cells[batch,:]=n_cells_batch
                n_counts_batch = arr_batch.sum(axis = 0).ravel()
                n_counts[batch,:]=n_counts_batch
            self.var["n_cells"] = cp.asnumpy(n_cells.sum(axis= 0).ravel())
            self.var["n_counts"] = cp.asnumpy(n_counts.sum(axis= 0).ravel())
        else:
            self.X = self.X.tocsc()
            n_cells = cp.diff(self.X.indptr).ravel()
            n_counts = self.X.sum(axis = 0).ravel()
            self.X = self.X.tocsr()
            self.var["n_cells"] = cp.asnumpy(n_cells)
            self.var["n_counts"] = cp.asnumpy(n_counts)


    def filter_genes(self, qc_var = "n_cells", min_count = None, max_count = None, batchsize = None, verbose =True):
        """
        Filter genes that have greater than a max number of genes or less than
        a minimum number of a feature in a given `.var` columns. Can so far only be used for numerical columns.
        You can run this function on 'n_cells' or 'n_counts' with a previous columns in `.var`.
        
        Parameters
        ----------
        qc_var: str (default: n_cells)
            column in `.var` with numerical entries to filter against
            
        min_count : float
            Lower bound on number of a given feature to keep gene

        max_count : float
            Upper bound on number of a given feature to keep gene
        
        batchsize: int (default: None)
            only needed if you run `filter_genes` before `calculate_qc` or `calc_gene_qc` on 'n_genes' or 'n_counts'. Number of rows to be processed together. This can be adjusted for performance to trade-off memory use.
            
        verbose: bool (default: True)
            Print number of discarded genes
        
        Returns
        -------
        a filtered cunnData object inplace
        
        """
        
        if qc_var in self.var.keys():
            if min_count is not None and max_count is not None:
                thr=np.where((self.var[qc_var] <= max_count) &  (min_count <= self.var[qc_var]))[0]
            elif min_count is not None:
                thr=np.where(self.var[qc_var] >= min_count)[0]
            elif max_count is not None:
                thr=np.where(self.var[qc_var] <= max_count)[0]

            if verbose:
                print(f"filtered out {self.var.shape[0]-thr.shape[0]} genes based on {qc_var}")
            self.X = self.X.tocsr()
            self.X = self.X[:, thr]
            self.X = self.X.tocsr()
            self.var = self.var.iloc[cp.asnumpy(thr)]
            
        elif qc_var in ["n_cells","n_counts"]:
            self.calc_gene_qc(batchsize = batchsize)    
            if min_count is not None and max_count is not None:
                thr=np.where((self.var[qc_var] <= max_count) &  (min_count <= self.var[qc_var]))[0]
            elif min_count is not None:
                thr=np.where(self.var[qc_var] >= min_count)[0]
            elif max_count is not None:
                thr=np.where(self.var[qc_var] <= max_count)[0]

            if verbose:
                print(f"filtered out {self.var.shape[0]-thr.shape[0]} genes based on {qc_var}")
            self.X = self.X.tocsr()
            self.X = self.X[:, thr]
            self.X = self.X.tocsr()
            self.var = self.var.iloc[cp.asnumpy(thr)]
        else:
            print(f"please check qc_var")


        
    def caluclate_qc(self, qc_vars = None, batchsize = None):
        """
        Calculates basic qc Parameters. Calculates number of genes per cell (n_genes) and number of counts per cell (n_counts).
        Loosly based on calculate_qc_metrics from scanpy [Wolf et al. 2018]. Updates .obs with columns with qc data.
        
        Parameters
        ----------
        qc_vars: str, list (default: None)
            Keys for boolean columns of .var which identify variables you could want to control for (e.g. Mito). Run flag_gene_family first
            
        batchsize: int (default: None)
            Number of rows to be processed together. This can be adjusted for performance to trade-off memory use.
            
        Returns
        -------
        adds the following columns in .obs
        n_counts
            number of counts per cell
        n_genes
            number of genes per cell
        for qc_var in qc_vars
            total_qc_var
                number of counts per qc_var (e.g total counts mitochondrial genes)
            percent_qc_vars
                
                Proportion of counts of qc_var (percent of counts mitochondrial genes)
        
        """      
        if batchsize:
            n_batches = math.ceil(self.X.shape[0] / batchsize)
            n_genes = []
            n_counts = []
            if "n_cells" not in self.var.keys() or  "n_counts" not in self.var.keys():
                self.calc_gene_qc(batchsize = batchsize)    
            if qc_vars:
                if type(qc_vars) is str:
                    qc_var_total = []
                    
                elif type(qc_vars) is list:
                    qc_var_total = []
                    for i in range(len(qc_vars)):
                        my_list = []
                        qc_var_total.append(my_list)
                        
            for batch in range(n_batches):
                batch_size = batchsize
                start_idx = batch * batch_size
                stop_idx = min(batch * batch_size + batch_size, self.X.shape[0])
                arr_batch = self.X[start_idx:stop_idx]
                n_genes.append(cp.diff(arr_batch.indptr).ravel().get())
                n_counts.append(arr_batch.sum(axis=1).ravel().get())
                if qc_vars:
                    if type(qc_vars) is str:
                        qc_var_total.append(arr_batch[:,self.var[qc_vars]].sum(axis=1).ravel().get())

                    elif type(qc_vars) is list:
                        for i in range(len(qc_vars)):
                             qc_var_total[i].append(arr_batch[:,self.var[qc_vars[i]]].sum(axis=1).ravel().get())
                        
                
            self.obs["n_genes"] = np.concatenate(n_genes)
            self.obs["n_counts"] = np.concatenate(n_counts)
            if qc_vars:
                if type(qc_vars) is str:
                    self.obs["total_"+qc_vars] = np.concatenate(qc_var_total)
                    self.obs["percent_"+qc_vars] =self.obs["total_"+qc_vars]/self.obs["n_counts"]*100
                elif type(qc_vars) is list:
                    for i in range(len(qc_vars)):
                        self.obs["total_"+qc_vars[i]] = np.concatenate(qc_var_total[i])
                        self.obs["percent_"+qc_vars[i]] =self.obs["total_"+qc_vars[i]]/self.obs["n_counts"]*100
        else:
            self.obs["n_genes"] = cp.asnumpy(cp.diff(self.X.indptr)).ravel()
            self.obs["n_counts"] = cp.asnumpy(self.X.sum(axis=1)).ravel()
            if "n_cells" not in self.var.keys() or  "n_counts" not in self.var.keys():
                self.calc_gene_qc(batchsize = None)    
            if qc_vars:
                if type(qc_vars) is str:
                    self.obs["total_"+qc_vars]=cp.asnumpy(self.X[:,self.var[qc_vars]].sum(axis=1))
                    self.obs["percent_"+qc_vars]=self.obs["total_"+qc_vars]/self.obs["n_counts"]*100

                elif type(qc_vars) is list:
                    for qc_var in qc_vars:
                        self.obs["total_"+qc_var]=cp.asnumpy(self.X[:,self.var[qc_var]].sum(axis=1))
                        self.obs["percent_"+qc_var]=self.obs["total_"+qc_var]/self.obs["n_counts"]*100
    
    def flag_gene_family(self, gene_family_name = str, gene_family_prefix = None, gene_list= None):
        """
        Flags a gene or gene_familiy in .var with boolean. (e.g all mitochondrial genes).
        Please only choose gene_family prefix or gene_list
        
        Parameters
        ----------
        gene_family_name: str
            name of colums in .var where you want to store informationa as a boolean
            
        gene_family_prefix: str
            prefix of the gene familiy (eg. mt- for all mitochondrial genes in mice)
            
        gene_list: list
            list of genes to flag in .var
        
        Returns
        -------
        adds the boolean column in .var 
        
        """
        if gene_family_prefix:
            self.var[gene_family_name] = cp.asnumpy(self.var.index.str.startswith(gene_family_prefix)).ravel()
        if gene_list:
            self.var[gene_family_name] = cp.asnumpy(self.var.index.isin(gene_list)).ravel()
    
    def filter_cells(self, qc_var, min_count=None, max_count=None, batchsize = None,verbose=True):
        """
        Filter cells that have greater than a max number of genes or less than
        a minimum number of a feature in a given .obs columns. Can so far only be used for numerical columns.
        It is recommended to run `calculated_qc` before using this function. You can run this function on n_genes or n_counts before running `calculated_qc`.
        
        Parameters
        ----------
        qc_var: str
            column in .obs with numerical entries to filter against
            
        min_count : float
            Lower bound on number of a given feature to keep cell

        max_count : float
            Upper bound on number of a given feature to keep cell
        
        batchsize: int (default: None)
            only needed if you run `filter_cells` before `calculate_qc` on 'n_genes' or 'n_counts'. Number of rows to be processed together. This can be adjusted for performance to trade-off memory use.
            
        verbose: bool (default: True)
            Print number of discarded cells
        
        Returns
        -------
        a filtered cunnData object inplace
        
        """
        if qc_var in self.obs.keys(): 
            inter = np.array
            if min_count is not None and max_count is not None:
                inter=np.where((self.obs[qc_var] < max_count) &  (min_count< self.obs[qc_var]))[0]
            elif min_count is not None:
                inter=np.where(self.obs[qc_var] > min_count)[0]
            elif max_count is not None:
                inter=np.where(self.obs[qc_var] < max_count)[0]
            else:
                print(f"Please specify a cutoff to filter against")
            if verbose:
                print(f"filtered out {self.obs.shape[0]-inter.shape[0]} cells")
            self.X = self.X[inter,:]
            self.obs = self.obs.iloc[inter]
        elif qc_var in ['n_genes','n_counts']:
            print(f"Running calculate_qc for 'n_genes' or 'n_counts'")
            self.caluclate_qc(batchsize=batchsize)
            inter = np.array
            if min_count is not None and max_count is not None:
                inter=np.where((self.obs[qc_var] < max_count) &  (min_count< self.obs[qc_var]))[0]
            elif min_count is not None:
                inter=np.where(self.obs[qc_var] > min_count)[0]
            elif max_count is not None:
                inter=np.where(self.obs[qc_var] < max_count)[0]
            else:
                print(f"Please specify a cutoff to filter against")
            if verbose:
                print(f"filtered out {self.obs.shape[0]-inter.shape[0]} cells")
            self.X = self.X[inter,:]
            self.obs = self.obs.iloc[inter]
        else:
            print(f"Please check qc_var.")
            

        
    def normalize_total(self, target_sum):
        """
        Normalizes rows in matrix so they sum to `target_sum`

        Parameters
        ----------

        target_sum : int
            Each row will be normalized to sum to this value
        
        
        Returns
        -------
        
        a normalized sparse Matrix to a specified target sum
        
        """
        csr_arr = self.X
        mul_kernel = cp.RawKernel(r'''
            extern "C" __global__
            void mul_kernel(const int *indptr, float *data, 
                            int nrows, int tsum) {
                int row = blockDim.x * blockIdx.x + threadIdx.x;

                if(row >= nrows)
                    return;

                float scale = 0.0;
                int start_idx = indptr[row];
                int stop_idx = indptr[row+1];

                for(int i = start_idx; i < stop_idx; i++)
                    scale += data[i];

                if(scale > 0.0) {
                    scale = tsum / scale;
                    for(int i = start_idx; i < stop_idx; i++)
                        data[i] *= scale;
                }
            }
            ''', 'mul_kernel')

        mul_kernel((math.ceil(csr_arr.shape[0] / 32.0),), (32,),
                       (csr_arr.indptr,
                        csr_arr.data,
                        csr_arr.shape[0],
                       int(target_sum)))

        self.X = csr_arr
    
    def log1p(self):
        """
        Calculated the natural logarithm of one plus the sparse marttix, element-wise inlpace in cunnData object.
        """
        self.X = self.X.log1p()
        self.uns["log1p"] = {"base": None}
        
    
    def highly_varible_genes(self,min_mean = 0.0125,max_mean =3,min_disp= 0.5,max_disp =np.inf, n_top_genes = None, flavor = 'seurat', n_bins = 20, batch_key = None):
        """
        Annotate highly variable genes. Expects logarithmized data. Reimplentation of scanpy's function. 
        Depending on flavor, this reproduces the R-implementations of Seurat, Cell Ranger.
        
        For these dispersion-based methods, the normalized dispersion is obtained by scaling with the mean and standard deviation of the dispersions for genes falling into a given bin for mean expression of genes. This means that for each bin of mean expression, highly variable genes are selected.
        
        Parameters
        ----------

        min_mean: float (default: 0.0125)
            If n_top_genes unequals None, this and all other cutoffs for the means and the normalized dispersions are ignored.
        max_mean: float (default: 3)
            If n_top_genes unequals None, this and all other cutoffs for the means and the normalized dispersions are ignored.
        min_disp: float (default: 0.5)
            If n_top_genes unequals None, this and all other cutoffs for the means and the normalized dispersions are ignored.
        max_disp: float (default: inf)
            If n_top_genes unequals None, this and all other cutoffs for the means and the normalized dispersions are ignored.
        n_top_genes: int (defualt: None)
            Number of highly-variable genes to keep.
        n_bins : int (default: 20)
            Number of bins for binning the mean gene expression. Normalization is done with respect to each bin. If just a single gene falls into a bin, the normalized dispersion is artificially set to 1. 
        flavor : {‘seurat’, ‘cell_ranger’} (default: 'seurat')
            Choose the flavor for identifying highly variable genes. For the dispersion based methods in their default workflows, Seurat passes the cutoffs whereas Cell Ranger passes n_top_genes.
        batch_key:
            If specified, highly-variable genes are selected within each batch separately and merged.
            
        
        Returns
        -------
        
        upates .var with the following fields
        highly_variablebool
            boolean indicator of highly-variable genes

        means
            means per gene

        dispersions
            dispersions per gene

        dispersions_norm
            normalized dispersions per gene
        
        """
        if batch_key is None:
            df = _highly_variable_genes_single_batch(
                self.X.tocsc(),
                min_disp=min_disp,
                max_disp=max_disp,
                min_mean=min_mean,
                max_mean=max_mean,
                n_top_genes=n_top_genes,
                n_bins=n_bins,
                flavor=flavor)
        else:
            self.obs[batch_key] = self.obs[batch_key].astype("category")
            batches = self.obs[batch_key].cat.categories
            df = []
            genes = self.var.index.to_numpy()
            for batch in batches:
                inter_matrix = self.X[np.where(self.obs[batch_key]==batch)[0],].tocsc()
                thr_org = cp.diff(inter_matrix.indptr).ravel()
                thr = cp.where(thr_org >= 1)[0]
                thr_2 = cp.where(thr_org < 1)[0]
                inter_matrix = inter_matrix[:, thr]
                thr = thr.get()
                thr_2 = thr_2.get()
                inter_genes = genes[thr]
                other_gens_inter = genes[thr_2]
                hvg_inter = _highly_variable_genes_single_batch(inter_matrix,
                                                                min_disp=min_disp,
                                                                max_disp=max_disp,
                                                                min_mean=min_mean,
                                                                max_mean=max_mean,
                                                                n_top_genes=n_top_genes,
                                                                n_bins=n_bins,
                                                                flavor=flavor)
                hvg_inter["gene"] = inter_genes
                missing_hvg = pd.DataFrame(
                    np.zeros((len(other_gens_inter), len(hvg_inter.columns))),
                    columns=hvg_inter.columns,
                )
                missing_hvg['highly_variable'] = missing_hvg['highly_variable'].astype(bool)
                missing_hvg['gene'] = other_gens_inter
                hvg = hvg_inter.append(missing_hvg, ignore_index=True)
                idxs = np.concatenate((thr, thr_2))
                hvg = hvg.loc[np.argsort(idxs)]
                df.append(hvg)
            
            df = pd.concat(df, axis=0)
            df['highly_variable'] = df['highly_variable'].astype(int)
            df = df.groupby('gene').agg(
                dict(
                    means=np.nanmean,
                    dispersions=np.nanmean,
                    dispersions_norm=np.nanmean,
                    highly_variable=np.nansum,
                )
            )
            df.rename(
                columns=dict(highly_variable='highly_variable_nbatches'), inplace=True
            )
            df['highly_variable_intersection'] = df['highly_variable_nbatches'] == len(
                batches
            )
            if n_top_genes is not None:
                # sort genes by how often they selected as hvg within each batch and
                # break ties with normalized dispersion across batches
                df.sort_values(
                    ['highly_variable_nbatches', 'dispersions_norm'],
                    ascending=False,
                    na_position='last',
                    inplace=True,
                )
                df['highly_variable'] = False
                df.highly_variable.iloc[:n_top_genes] = True
                df = df.loc[genes]
            else:
                df = df.loc[genes]
                dispersion_norm = df.dispersions_norm.values
                dispersion_norm[np.isnan(dispersion_norm)] = 0  # similar to Seurat
                gene_subset = np.logical_and.reduce(
                    (
                        df.means > min_mean,
                        df.means < max_mean,
                        df.dispersions_norm > min_disp,
                        df.dispersions_norm < max_disp,
                    )
                )
                df['highly_variable'] = gene_subset
        
        self.var["highly_variable"] =df['highly_variable'].values
        self.var["means"] = df['means'].values
        self.var["dispersions"]=df['dispersions'].values
        self.var["dispersions_norm"]=df['dispersions_norm'].values
        self.uns['hvg'] = {'flavor': flavor}
        if batch_key is not None:
            self.var['highly_variable_nbatches'] = df[
                'highly_variable_nbatches'
            ].values
            self.var['highly_variable_intersection'] = df[
                'highly_variable_intersection'
            ].values
    

        
    def filter_highly_variable(self):
        """
        Filters the cunndata object for highly_variable genes. Run highly_varible_genes first.
        
        Returns
        -------
        
        updates cunndata object to only contain highly variable genes.
        
        """
        if "highly_variable" in self.var.keys():
            thr = np.where(self.var["highly_variable"] == True)[0]
            self.X =self.X.tocsc()
            self.X = self.X[:, thr]
            self.var = self.var.iloc[cp.asnumpy(thr)]      
        else:
            print(f"Please calculate highly variable genes first")
            
    def regress_out(self, keys, verbose=False):

        """
        Use linear regression to adjust for the effects of unwanted noise
        and variation. 
        Parameters
        ----------

        adata
            The annotated data matrix.
        keys
            Keys for numerical observation annotation on which to regress on.

        verbose : bool
            Print debugging information

        Returns
        -------
        updates cunndata object with the corrected data matrix


        """
        
        if type(self.X) is not cpx.scipy.sparse.csc.csc_matrix:
            self.X = self.X.tocsc()

        dim_regressor= 2
        if type(keys)is list:
            dim_regressor = len(keys)+1

        regressors = cp.ones((self.X.shape[0]*dim_regressor)).reshape((self.X.shape[0], dim_regressor), order="F")
        if dim_regressor==2:
            regressors[:, 1] = cp.array(self.obs[keys]).ravel()
        else:
            for i in range(dim_regressor-1):
                regressors[:, i+1] = cp.array(self.obs[keys[i]]).ravel()

        outputs = cp.empty(self.X.shape, dtype=self.X.dtype, order="F")

        if self.X.shape[0] < 100000 and cpx.scipy.sparse.issparse(self.X):
            self.X = self.X.todense()
        
        for i in range(self.X.shape[1]):
            if verbose and i % 500 == 0:
                print("Regressed %s out of %s" %(i, self.X.shape[1]))
            X = regressors
            y = self.X[:,i]
            outputs[:, i] = _regress_out_chunk(X, y)
        self.X = outputs
    
    
    def scale(self, max_value=10):
        """
        Scales matrix to unit variance and clips values
        Parameters
        ----------
        max_value : int
                    After scaling matrix to unit variance,
                    values will be clipped to this number
                    of std deviations.
        Return
        ------
        updates cunndata object with a scaled cunndata.X
        """
        if type(self.X) is not cp._core.core.ndarray:
            print("densifying _.X")
            X = self.X.toarray()
        else:
            X =self.X
        mean = X.mean(axis=0)
        X -= mean
        del mean
        stddev = cp.sqrt(X.var(axis=0))
        X /= stddev
        del stddev
        self.X = cp.clip(X,a_max=max_value)
        
def _regress_out_chunk(X, y):
    """
    Performs a data_cunk.shape[1] number of local linear regressions,
    replacing the data in the original chunk w/ the regressed result.

    Parameters
    ----------

    X : cupy.ndarray of shape (n_cells, 3)
        Matrix of regressors

    y : cupy.sparse.spmatrix of shape (n_cells,)
        Sparse matrix containing a single column of the cellxgene matrix

    Returns
    -------

    dense_mat : cupy.ndarray of shape (n_cells,)
        Adjusted column
    """
    if cp.sparse.issparse(y):
        y = y.todense()

    lr = LinearRegression(fit_intercept=False, output_type="cupy")
    lr.fit(X, y, convert_dtype=True)
    return y.reshape(y.shape[0],) - lr.predict(X).reshape(y.shape[0])

def _highly_variable_genes_single_batch(my_mat,min_mean = 0.0125,max_mean =3,min_disp= 0.5,max_disp =np.inf, n_top_genes = None, flavor = 'seurat', n_bins = 20):
        """\
        See `highly_variable_genes`.
        Returns
        -------
        A DataFrame that contains the columns
        `highly_variable`, `means`, `dispersions`, and `dispersions_norm`.
        """
        if flavor == 'seurat':
            my_mat = my_mat.expm1()
        mean = (my_mat.sum(axis =0)/my_mat.shape[0]).ravel()
        mean[mean == 0] = 1e-12
        my_mat.data **= 2
        inter = (my_mat.sum(axis =0)/my_mat.shape[0]).ravel()
        var = inter - mean ** 2
        disp = var/mean
        if flavor == 'seurat':  # logarithmized mean as in Seurat
            disp[disp == 0] = np.nan
            disp = np.log(disp)
            mean = np.log1p(mean)
        df = pd.DataFrame()
        mean = mean.get()
        disp = disp.get()
        df['means'] = mean
        df['dispersions'] = disp
        if flavor == 'seurat':
            df['mean_bin'] = pd.cut(df['means'], bins=n_bins)
            disp_grouped = df.groupby('mean_bin')['dispersions']
            disp_mean_bin = disp_grouped.mean()
            disp_std_bin = disp_grouped.std(ddof=1)
            # retrieve those genes that have nan std, these are the ones where
            # only a single gene fell in the bin and implicitly set them to have
            # a normalized disperion of 1
            one_gene_per_bin = disp_std_bin.isnull()
            gen_indices = np.where(one_gene_per_bin[df['mean_bin'].values])[0].tolist()

            # Circumvent pandas 0.23 bug. Both sides of the assignment have dtype==float32,
            # but there’s still a dtype error without “.value”.
            disp_std_bin[one_gene_per_bin.values] = disp_mean_bin[
                one_gene_per_bin.values
            ].values
            disp_mean_bin[one_gene_per_bin.values] = 0
            # actually do the normalization
            df['dispersions_norm'] = (
                df['dispersions'].values  # use values here as index differs
                - disp_mean_bin[df['mean_bin'].values].values
            ) / disp_std_bin[df['mean_bin'].values].values

        elif flavor == 'cell_ranger':
            from statsmodels import robust
            df['mean_bin'] = pd.cut(
                    df['means'],
                    np.r_[-np.inf, np.percentile(df['means'], np.arange(10, 105, 5)), np.inf],
                )
            disp_grouped = df.groupby('mean_bin')['dispersions']
            disp_median_bin = disp_grouped.median()
            with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    disp_mad_bin = disp_grouped.apply(robust.mad)
                    df['dispersions_norm'] = (
                        df['dispersions'].values - disp_median_bin[df['mean_bin'].values].values
                    ) / disp_mad_bin[df['mean_bin'].values].values

        dispersion_norm = df['dispersions_norm'].values
        if n_top_genes is not None:
            dispersion_norm = dispersion_norm[~np.isnan(dispersion_norm)]
            dispersion_norm[::-1].sort()# interestingly, np.argpartition is slightly slower
            if n_top_genes > my_mat.shape[1]:
                n_top_genes = my_mat.shape[1]
            disp_cut_off = dispersion_norm[n_top_genes - 1]
            gene_subset = np.nan_to_num(df['dispersions_norm'].values) >= disp_cut_off
        else:
            dispersion_norm[np.isnan(dispersion_norm)] = 0  # similar to Seurat
            gene_subset = np.logical_and.reduce(
                (
                    mean > min_mean,
                    mean < max_mean,
                    dispersion_norm > min_disp,
                    dispersion_norm < max_disp,
                )
            )

        df['highly_variable'] = gene_subset
        return df