'''
    Created on Sep 29, 2011
    
    @author: ksahlin
    
    This file is part of BESST.
    
    BESST is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
    
    BESST is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.
    
    You should have received a copy of the GNU General Public License
    along with BESST.  If not, see <http://www.gnu.org/licenses/>.
    '''

import sys
from collections import defaultdict
import time

import networkx as nx
from networkx import algorithms
import multiprocessing

import Contig, Scaffold, Parameter
import GenerateOutput as GO
import GapCalculator as GC
from Norm import normpdf, normcdf
import ExtendLargeScaffolds as ELS


def constant_large():
    return 2 ** 32
def constant_small():
    return -1


def Algorithm(G, G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, Information, param):
    #search for linear streches in the graph, remove all cliques >2 and all contigs having more than three neighbors
    nr_edges = 0
    for edge in G.edges_iter():
        if G[edge[0]][edge[1]]['nr_links']:
            nr_edges += 1

    print >> Information, str(nr_edges) + ' link edges created.'
    print 'Perform inference on scaffold graph...'
    #VizualizeGraph(G,param,Information)

    if param.detect_haplotype:
        HaplotypicRegions(G, G_prime, Contigs, Scaffolds, param)
    #save graph in dot format to file here

    ##If sigma specified. Pre calculate a look up table for every possible gap estimate in the common case
    ##where we have two long contigs that are linked. We do this one time per library to save computation time.
    if param.std_dev_ins_size:
        dValuesTable = GC.PreCalcMLvaluesOfdLongContigs(param.mean_ins_size, param.std_dev_ins_size, param.read_len)
    else :
        dValuesTable = None
    already_visited = set()
    if param.extend_paths:
        for node in G:
            already_visited.add(node)


    ##### Here is the scaffolding algorithm #######

    G = RemoveIsolatedContigs(G, Information)     #step1
    RemoveAmbiguousRegionsUsingScore(G, G_prime, Information, param) #step2
    G = RemoveIsolatedContigs(G, Information) #there are probably new isolated nodes created from step 2
    G, Contigs, Scaffolds = RemoveLoops(G, G_prime, Scaffolds, Contigs, Information, param)    #step4    
    #The contigs that made it to proper scaffolds
    (Contigs, Scaffolds, param) = NewContigsScaffolds(G, G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, Information, dValuesTable, param, already_visited)   #step5
    ##Here PathExtension algorithm between created scaffolds is called if PRO is activated

    if param.extend_paths:
        print '\n\n\n Searching for paths BETWEEN scaffolds\n\n\n'
        #TODO: Whenever we have removed an edge in G in algm above (deduced it as spurious), we should remove the same edge in G_prime. 
        #Also: we must update G_prime and G with the new scaffold objects created in this step. The isolated nodes removed here should not
        #be removed from G_prime
        PROBetweenScaf(G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, param, dValuesTable)
        print 'Nr of contigs left: ', len(G_prime.nodes()) / 2.0, 'Nr of linking edges left:', len(G_prime.edges()) - len(G_prime.nodes()) / 2.0
        for node in G_prime.nodes():
            nbrs = G_prime.neighbors(node)
            for nbr in nbrs:
                if G_prime[node][nbr]['nr_links'] and 'score' not in G_prime[node][nbr]:
                    G_prime.remove_edge(node, nbr)

        RemoveAmbiguousRegionsUsingScore(G_prime, G_prime, Information, param)
        G_prime, Contigs, Scaffolds = RemoveLoops(G_prime, G_prime, Scaffolds, Contigs, Information, param)
        for node in G_prime:
            if node[0] not in Scaffolds:
                scaf_obj = small_scaffolds[node[0]]
                Scaffolds[node[0]] = scaf_obj
                cont_objects = scaf_obj.contigs
                for obj_ in cont_objects:
                    ctg_name = obj_.name
                    Contigs[ctg_name] = obj_
                    del small_contigs[ctg_name]
                del small_scaffolds[node[0]]
        (Contigs, Scaffolds, param) = NewContigsScaffolds(G_prime, G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, Information, dValuesTable, param, already_visited)


    ####### End of algorithm #####################


    return()

def VizualizeGraph(G, param, Information):
    import os
    try:
        import matplotlib
        matplotlib.use('Agg')

        try:
            os.mkdir(param.output_directory + '/graph_regions' + str(int(param.mean_ins_size)))
        except OSError:
            #directory is already created
            pass
        counter = 1
        import copy

        G_copy = copy.deepcopy(G)
        RemoveIsolatedContigs(G_copy, Information)
        #        nx.draw(G_copy)
        #        matplotlib.pyplot.savefig(param.output_directory +'graph_regions'+str(int(param.mean_ins_size))+'/'+str(counter)+'.png')
#        CB = nx.cycle_basis(G)
#        print 'NR of SubG: ' ,len(CB)
        CB = nx.connected_component_subgraphs(G_copy)
        print G_copy.edges()
        for cycle in CB:
#            if len(cycle) >= 6: # at leats 6 nodes if proper cycle (haplotypic region)
            #print 'CYCLE:',cycle.edges()
            #subgraph = nx.Graph()
            #subgraph.add_edges_from(cycle)
            #print 'Hej',subgraph.edges()
            nx.draw(cycle)
            matplotlib.pyplot.savefig(param.output_directory + 'graph_regions' + str(int(param.mean_ins_size)) + '/' + str(counter) + '.png')
            matplotlib.pyplot.clf()
            counter += 1
    except ImportError:
        pass
    return()

def RemoveIsolatedContigs(G, Information):
    print 'Remove isolated nodes.'
    counter = 0
    for node in G.nodes():
        if node in G:
            nbr = G.neighbors(node)[0]
            if len(G.neighbors(node)) == 1 and len(G.neighbors(nbr)) == 1:
                counter += 1
                G.remove_nodes_from([node, nbr])
    print >> Information, str(counter) + ' isolated contigs removed from graph.'
    return(G)


def HaplotypicRegions(G, G_prime, Contigs, Scaffolds, param):
    CB = nx.cycle_basis(G)
    print 'NR of cycles bases: ' , len(CB)
    cb_6 = 0
    cb_6_true = 0
    cb_else_true = 0
    potentially_merged = 0
    tot_nr_contigs_merged = 0
    strange_cases = 0

    for cycle in CB:
        str_case_abort = False
        contigs = [node[0] for node in cycle]
        d = {}

####### Very temporary implementation of dealing with haplotypes!!! #########            
        if len(cycle) >= 6:
            for i in contigs: d[i] = d.has_key(i)
            singles = [k for k in d.keys() if not d[k]]
            if len(singles) == 2:
                cb_else_true += 1
                #find length of the two paths between the source and sink
                haplotype_region = True
                #first path
                #get sink and source
                try:
                    cycle.index((singles[0], 'L'))
                    source_node = (singles[0], 'L')
                except ValueError:
                    source_node = (singles[0], 'R')
                try:
                    cycle.index((singles[1], 'L'))
                    sink_node = (singles[1], 'L')
                except ValueError:
                    sink_node = (singles[1], 'R')

                #check if region has been removed in some previuos step: this suggests some strange region
                for scaf in cycle:
                    if not scaf[0] in Scaffolds:
                        strange_cases += 1
                        #print 'STRANGE!'
                        str_case_abort = True
                if str_case_abort:
                    continue

                sub_G = nx.subgraph(G, cycle)
                path = nx.algorithms.shortest_path(sub_G, source=source_node, target=sink_node)


                ## Get length of path (OBS: gaps not implemented yet!!) ##
                #print 'First path' ,path
                length_path1 = 0
                nr_contigs_path1 = 0
                for scaffold_ in path:
                    scaffold = scaffold_[0]
                    if sink_node != scaffold_ and source_node != scaffold_:
                        length_path1 += Scaffolds[scaffold].s_length
                        nr_contigs = len(Scaffolds[scaffold].contigs)
                        #if nr_contigs > 1:
                        #    print 'More than one contig in scaffold, contigs are:'
                        for cont_obj in Scaffolds[scaffold].contigs:
                            nr_contigs_path1 += 1
                            #print 'Haplotype: ', cont_obj.is_haplotype, cont_obj.coverage
                            if not cont_obj.is_haplotype:
                        #        print 'Not haplotype'
                                haplotype_region = False



                #print 'Total length of path 1: ', length_path1/2.0

                set_of_nodes = set(path)
                start_end = set([source_node, sink_node])
                tot_set = set(cycle)
                nodes_to_remove = set_of_nodes.symmetric_difference(start_end)
                remaining_path = tot_set.symmetric_difference(nodes_to_remove)
                #print 'Secont path', remaining_path
                length_path2 = 0
                nr_contigs_path2 = 0
                for scaffold_ in remaining_path:
                    scaffold = scaffold_[0]
                    if sink_node != scaffold_ and source_node != scaffold_:
                        length_path2 += Scaffolds[scaffold].s_length
                        nr_contigs = len(Scaffolds[scaffold].contigs)
                        #if nr_contigs > 1:
                        #    print 'More than one contig in scaffold'
                        for cont_obj in Scaffolds[scaffold].contigs:
                            nr_contigs_path2 += 1
                            #print 'Haplotype: ', cont_obj.is_haplotype, cont_obj.coverage
                            if not cont_obj.is_haplotype:
                                haplotype_region = False

                try:
                    if length_path2 / float(length_path1) < param.hapl_ratio or length_path1 / float(length_path2) > 1 / param.hapl_ratio and haplotype_region:
                        potentially_merged += 1
                        tot_nr_contigs_merged += nr_contigs_path2 / 2.0
                        #Remove all contigs from path 2
                        to_remove = remaining_path.symmetric_difference(start_end)
                        G.remove_nodes_from(to_remove)
                        G_prime.remove_nodes_from(to_remove)
                        for node in to_remove:
                            try: #remove scaffold with all contigs
                                for contig_obj in Scaffolds[node[0]].contigs:
                                    del Contigs[contig_obj.name]
                                del Scaffolds[node[0]]
                            except KeyError: #scaffold and all contigs has already been removed since to_revove-path contains two nodes for each scaffold
                                pass
                except ZeroDivisionError:
                    pass

    print 'NR of other interesting cycles: ', cb_else_true
    print 'Potential hapl regions treated: ', potentially_merged
    print 'Potential hapl contigs "removed": ', tot_nr_contigs_merged
    print 'Nr of strange cases (contigs occurring in multiple regions): ', strange_cases
    return()

def RemoveAmbiguousRegionsUsingScore(G, G_prime, Information, param):
    print 'Remove edges from node if more than two edges'
    counter1 = 0
    for node in G:
        nbrs = G.neighbors(node)
        #Remove ambiguous edges
        if len(nbrs) > 2:
            score_list = []
            for nbr in nbrs:
                if G[node][nbr]['nr_links']:
                    if 'score' not in G[node][nbr]:
                        sys.stderr.write(str(G[node][nbr]))
                    score_list.append((G[node][nbr]['score'], nbr))

            score_list.sort()

            if score_list[-1][0] > 0:
            ### save the dominating link edge on this side of the contig
                nr_nbrs = len(score_list)
                for i in xrange(0, nr_nbrs - 1):
                    G.remove_edge(node, score_list[i][1])
                    if param.extend_paths:
                        try: #we might have been removed this edge from G_prime when we did individual filtering of G_prime in CreateGraph module
                            G_prime.remove_edge(node, score_list[i][1])
                        except nx.exception.NetworkXError:
                            pass
            else:
                nr_nbrs = len(score_list)
                for i in xrange(0, nr_nbrs):
                    G.remove_edge(node, score_list[i][1])
                    if param.extend_paths:
                        try: #we might have been removed this edge from G_prime when we did individual filtering of G_prime in CreateGraph module
                            G_prime.remove_edge(node, score_list[i][1])
                        except nx.exception.NetworkXError:
                            pass
            counter1 += 1
        else:
            for nbr in nbrs:
                if G[node][nbr]['nr_links']:
                    if 'score' not in G[node][nbr]:
                        sys.stderr.write(str(G[node][nbr]))
                    if G[node][nbr]['score'] > 0:
                        pass
                    else:
                        G.remove_edge(node, nbr)
                        if param.extend_paths:
                            try: #we might have been removed this edge from G_prime when we did individual filtering of G_prime in CreateGraph module
                                G_prime.remove_edge(node, nbr)
                            except nx.exception.NetworkXError:
                                pass


    print >> Information, str(counter1) + ' ambiguous regions in graph ( a contig with more than 2 neighbors).'

    return()






def RemoveLoops(G, G_prime, Scaffolds, Contigs, Information, param):
#### After the proceure above, we hope that the graph is almost perfectly linear but we can still be encountering cycles (because of repeats or haplotypic contigs that has slipped through our conditions). Thus we finally search for loops
    print 'Contigs/scaffolds left:', len(G.nodes()) / 2
    print 'Remove remaining cycles...'
    graphs = nx.connected_component_subgraphs(G)
    #print 'Nr connected components',len(graphs)
    counter = 0
    for graph in graphs:
        list_of_cycles = algorithms.cycles.cycle_basis(graph)
        for cycle in list_of_cycles:
            print >> Information, 'A cycle in the scaffold graph: ' + str(cycle) + '\n'
            print 'A cycle in the scaffold graph: ' + str(cycle), graph.edges()
            counter += 1
            for node in cycle:
                if node in G:
                    #we split up the whole cycle into separate contigs and send them to F
                    scaffold_ = node[0]
                    G.remove_nodes_from([(scaffold_, 'L'), (scaffold_, 'R')])
                    if param.extend_paths:
                        G_prime.remove_nodes_from([(scaffold_, 'L'), (scaffold_, 'R')])
#                    S_obj=Scaffolds[scaffold_]
#                    list_of_contigs=S_obj.contigs   #list of contig objects contained in scaffold object
#                    Contigs, F = GO.WriteToF(F,Contigs,list_of_contigs)
#                    del Scaffolds[scaffold_]
    print >> Information, str(counter) + ' cycles removed from graph.'
    return(G, Contigs, Scaffolds)

def NewContigsScaffolds(G, G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, Information, dValuesTable, param, already_visited):
### Remaining scaffolds are true sensible scaffolds, we must now update both the library of scaffold objects and the library of contig objects
    new_scaffolds_ = nx.connected_component_subgraphs(G)
    print 'Nr of new scaffolds created: ' + str(len(new_scaffolds_))
    print >> Information, 'Nr of new scaffolds created in this step: ' + str(len(new_scaffolds_))
    for new_scaffold_ in new_scaffolds_:
        param.scaffold_indexer += 1
        #scaf_size=len(new_scaffold_)
        scaffold_length = 0
        contig_list = []
        #Store nr_of links between contigs before "destroying" the graph
#        for edge in new_scaffold_.edges_iter():
#            nr_links=G[edge[0]][edge[1]]['nr_links']
#            side1=edge[0][1]
#            side2=edge[1][1]
#            if nr_links:
#                contig_objects1=Scaffolds[edge[0][0]].contigs
#                contig_objects2=Scaffolds[edge[1][0]].contigs
#                GiveLinkConnection(Contigs,contig_objects1,contig_objects2,side1,side2,nr_links) 

        ##### Here PathExtension algorithm is called if PRO is activated #####
        if param.extend_paths:
            PROWithinScaf(G, G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, param, new_scaffold_, dValuesTable, already_visited)

        for node in new_scaffold_:
            if len(G.neighbors(node)) == 1:
                start = node
                break
        for node in new_scaffold_:
            if len(G.neighbors(node)) == 1 and node != start:
                end = node
        #Create info to new scaffold object such as total length and the contig objects included

        prev_node = ('', '')
        pos = 0
        (G, contig_list, scaffold_length) = UpdateInfo(G, Contigs, small_contigs, Scaffolds, small_scaffolds, start, prev_node, pos, contig_list, scaffold_length, dValuesTable, param)
        S = Scaffold.scaffold(param.scaffold_indexer, contig_list, scaffold_length, defaultdict(constant_large), defaultdict(constant_large), defaultdict(constant_small), defaultdict(constant_small))  #Create the new scaffold object 

        Scaffolds[S.name] = S        #include in scaffold library

        if param.extend_paths:
            # Find the ends of the old subgraph new_scaffold_. We want them to be able to relabel these end nodes as the new sides on the new scaffold object created
            #only these ends are allowed to have links because they are of size mean+ 4*sigma so nothing is supposed to span over.

            #add the new scaffold object to G_prime

            G_prime.add_node((S.name, 'L'))  #start node
            G_prime.add_node((S.name, 'R'))  # end node
            G_prime.add_edge((S.name, 'L'), (S.name, 'R'), nr_links=None)
            try:
                for nbr in G_prime.neighbors(start):
                    nr_links_ = G_prime[start][nbr]['nr_links']
                    if nr_links_:
                        obs_ = G_prime[start][nbr]['obs']
                        G_prime.add_edge((S.name, 'L'), nbr, nr_links=nr_links_, obs=obs_)

                for nbr in G_prime.neighbors(end):
                    nr_links_ = G_prime[end][nbr]['nr_links']
                    if nr_links_:
                        obs_ = G_prime[end][nbr]['obs']
                        G_prime.add_edge((S.name, 'R'), nbr, nr_links=nr_links_, obs=obs_)

                #remove the old scaffold objects from G_prime
                G_prime.remove_nodes_from(new_scaffold_)
            except nx.exception.NetworkXError:
                pass

    return(Contigs, Scaffolds, param)


def UpdateInfo(G, Contigs, small_contigs, Scaffolds, small_scaffolds, node, prev_node, pos, contig_list, scaffold_length, dValuesTable, param):
    scaf = node[0]
    side = node[1]
    prev_scaf = prev_node[0]
    if len(G.neighbors((scaf, side))) == 0:  #reached end of scaffol
        #find the contig with the largest position
        object_with_largest_pos_in_scaffold = max(contig_list, key=lambda object: object.position + object.length)
        scaffold_length = object_with_largest_pos_in_scaffold.position + object_with_largest_pos_in_scaffold.length
#        try:
        del Scaffolds[scaf] #finally, delete the old scaffold object
        G.remove_node((scaf, side))
#        except KeyError:
#            del small_scaffolds[scaf] #finally, delete the old scaffold object
        return(G, contig_list, scaffold_length)
    else:
        nbr_node = G.neighbors((scaf, side))
        nbr_scaf = nbr_node[0][0]
        nbr_side = nbr_node[0][1]
        if scaf != prev_scaf:
            if side == 'L':    #Contig/scaffold still has same orientation as in previous iteration, just update position in scaffold                                           
                #want to assign nr of links to contig object, note that in case of a "multiple contigs"-scaffold object, only the outermost contig holds the information of the total nr of links between the two scaffold objects
                #try:
                contig_objects = Scaffolds[scaf].contigs #list of contig objects
                #except KeyError:
                #    contig_objects=small_scaffolds[scaf].contigs #list of contig objects
                #Update just update position in scaffold 
                for contig in contig_objects:
                    contig.scaffold = param.scaffold_indexer
                    contig.position += pos
                    #direction unchanged
                    contig_list.append(contig)
                G.remove_node((scaf, side))
                prev_node = node
                node = (nbr_scaf, nbr_side)
                #try:                 
                pos += Scaffolds[scaf].s_length  #update position before sending it to next scaffold
                #except KeyError:
                #    pos+=small_scaffolds[scaf].s_length  #update position before sending it to next scaffold

                G, contig_list, scaffold_length = UpdateInfo(G, Contigs, small_contigs, Scaffolds, small_scaffolds, node, prev_node, pos, contig_list, scaffold_length, dValuesTable, param)

            else:  #Contig/scaffold need to change orientation as well as modify orientation in this case
                #try:
                contig_objects = Scaffolds[scaf].contigs #list of contig objects
                #except KeyError:
                #    contig_objects=small_scaffolds[scaf].contigs #list of contig objects

                for contig in contig_objects:
                    contig.scaffold = param.scaffold_indexer
                    #try:
                    curr_scaf_length = Scaffolds[scaf].s_length
                    #except KeyError:
                    #    curr_scaf_length=small_scaffolds[scaf].s_length

                    curr_pos_within_scaf = contig.position
                    contig_length = contig.length
                    contig.position = pos + (curr_scaf_length - curr_pos_within_scaf) - contig_length #updates the position within scaf
                    contig.direction = bool(True -contig.direction) #changes the direction
                    contig_list.append(contig)

                G.remove_node((scaf, side))
                prev_node = node
                node = (nbr_scaf, nbr_side)
                #try:                    
                pos += Scaffolds[scaf].s_length  #update position before sending it to next scaffold
                #except KeyError:
                #pos+=small_scaffolds[scaf].s_length  #update position before sending it to next scaffold

                G, contig_list, scaffold_length = UpdateInfo(G, Contigs, small_contigs, Scaffolds, small_scaffolds, node, prev_node, pos, contig_list, scaffold_length, dValuesTable, param)
        else:
            #calculate gap to next scaffold
            sum_obs = G[(scaf, side)][(nbr_scaf, nbr_side)]['obs']
            nr_links = G[(scaf, side)][(nbr_scaf, nbr_side)]['nr_links']
            data_observation = (nr_links * param.mean_ins_size - sum_obs) / float(nr_links)
            #try:
            c1_len = Scaffolds[scaf].s_length
            #except KeyError:
            #    c1_len=small_scaffolds[scaf].s_length
            #try:
            c2_len = Scaffolds[nbr_scaf].s_length
            #except KeyError:
            #   c2_len=small_scaffolds[nbr_scaf].s_length
            #do fancy gap estimation by the bias estimator formula
            if param.std_dev_ins_size and nr_links >= 5:
                #pre calculated value in lookup table 
                if c1_len > param.mean_ins_size + 4 * param.std_dev_ins_size and c2_len > param.mean_ins_size + 4 * param.std_dev_ins_size:
                    #(heuristic scale down of table to gaps of at most 2 stddevs away from mean)
                    try:
                        avg_gap = dValuesTable[int(round(data_observation, 0))]
                    except KeyError:
                        avg_gap = GC.GapEstimator(param.mean_ins_size, param.std_dev_ins_size, param.read_len, data_observation, c1_len, c2_len)
                        print 'Gap estimate was outside the boundary of the precalculated table, obs were: ', data_observation, 'binary search gave: ', avg_gap
                        #print 'Gap estimate was outside the boundary of the precalculated table'
                        #print 'Boundaries were [' ,-2*param.std_dev_ins_size, ' , ',param.mean_ins_size+2*param.std_dev_ins_size, ' ]. Observation were: ', data_observation
                        #if data_observation < -2*param.std_dev_ins_size:                            
                        #    avg_gap=int(-2*param.std_dev_ins_size)
                        #    print 'Setting gap estimate to min value:', avg_gap,'number of links: ' , nr_links
                        #else:
                        #    avg_gap=int(param.mean_ins_size+2*param.std_dev_ins_size-2*param.read_len)
                        #    print 'Setting gap estimate to max value:', avg_gap,'number of links: ' , nr_links
                #Do binary search for ML estimate of gap
                else:
                    avg_gap = GC.GapEstimator(param.mean_ins_size, param.std_dev_ins_size, param.read_len, data_observation, c1_len, c2_len)
                    #print avg_gap
            #do naive gap estimation
            else:
                avg_gap = int(data_observation)
            if avg_gap < 0:
                #TODO: Eventually implement SW algm to find ML overlap
                avg_gap = 0
            pos += int(avg_gap)
            G.remove_node((scaf, side))
            prev_node = node
            node = (nbr_scaf, nbr_side)
            #try:
            del Scaffolds[scaf] #finally, delete the old scaffold object
            #except KeyError:
            #    del small_scaffolds[scaf] #finally, delete the old scaffold object
            G, contig_list, scaffold_length = UpdateInfo(G, Contigs, small_contigs, Scaffolds, small_scaffolds, node, prev_node, pos, contig_list, scaffold_length, dValuesTable, param)
    return(G, contig_list, scaffold_length)

def PROWithinScaf(G, G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, param, new_scaffold_, dValuesTable, already_visited):
    #loc_count = 0
    for edge in new_scaffold_.edges_iter():
        nr_links_ = G[edge[0]][edge[1]]['nr_links']
        if nr_links_:
            start = edge[0]
            end = edge[1]
            c1_len = Scaffolds[start[0]].s_length
            c2_len = Scaffolds[end[0]].s_length
            sum_obs = G[start][end]['obs']
            data_observation = (nr_links_ * param.mean_ins_size - sum_obs) / float(nr_links_)
            avg_gap = GC.GapEstimator(param.mean_ins_size, param.std_dev_ins_size, param.read_len, data_observation, c1_len, c2_len)

            high_score_path, bad_links, score, path_len = ELS.WithinScaffolds(G, G_prime, start, end, already_visited, param.ins_size_threshold)
            if len(high_score_path) > 1:
                #print 'Start scaf:',start, 'End scaf:', end, 'Avg gap between big:', avg_gap,'Path:',high_score_path, 'nr bad links path:',bad_links, 'Score:',score,'path length:', path_len
                if score >= 0.0:
                    #loc_count += 1
                    #remove edge in G to fill in the small scaffolds
                    G.remove_edge(start, end)
                    #add small scaffolds to G
                    for i in range(0, len(high_score_path) - 1):
                        nr_lin = G_prime[high_score_path[i]][high_score_path[i + 1]]['nr_links']
                        try:
                            total_dist = G_prime[high_score_path[i]][high_score_path[i + 1]]['obs']
                            G.add_edge(high_score_path[i], high_score_path[i + 1], nr_links=nr_lin, obs=total_dist)
                        except KeyError:
                            G.add_edge(high_score_path[i], high_score_path[i + 1], nr_links=nr_lin)
                    #remove the small contigs from G_prime
                    G_prime.remove_nodes_from(high_score_path[1:-1])
                    # move all contig and scaffold objects from "small" structure to large structure to fit with UpdateInfo structure
                    small_scafs = map(lambda i: high_score_path[i], filter(lambda i: i % 2 == 1, range(len(high_score_path) - 1)))
                    for item in small_scafs:
                        scaf_obj = small_scaffolds[item[0]]
                        Scaffolds[item[0]] = scaf_obj
                        cont_objects = scaf_obj.contigs
                        for obj_ in cont_objects:
                            ctg_name = obj_.name
                            Contigs[ctg_name] = obj_
                            del small_contigs[ctg_name]
                        del small_scaffolds[item[0]]

    ####################################################################
    return()





def PROBetweenScaf(G_prime, Contigs, small_contigs, Scaffolds, small_scaffolds, param, dValuesTable):
    start_scaf_index = param.scaffold_indexer
    G = nx.Graph()
    for node in G_prime:
        if node[0] in Scaffolds: # meets the length criteria
            G.add_node(node)

    # Filtering and heuristic here to reduce computation if needed O(n^2) in contigs on pathfinder

    #remove all solated contigs
    print 'Remove isolated nodes.'
    for node in G.nodes():
        if node in G:
            nbr = G_prime.neighbors(node)[0]
            if len(G_prime.neighbors(node)) == 1 and len(G_prime.neighbors(nbr)) == 1:
                G.remove_nodes_from([node, nbr])


    if len(G.nodes()) / 2.0 > 10000:
        # Too few short contigs compared to long (ratio set to 0.1) or lib ins size + 2*std_dev - 2*read_len < 200 ) and too many large contigs (> 10 000) do not enter path extension algm since to low payoff:

        if len(small_scaffolds) / float(len(Scaffolds)) < 0.1:
            print "Did not enter path seartching algorithm between scaffolds due to too small fraction of small scaffolds, fraction were: ", len(small_scaffolds) / float(len(Scaffolds))
            return(start_scaf_index)

    ########### Find paths between scaffolds here ###############

    # Multi Processing (if available), check nr of available cores
    num_cores = multiprocessing.cpu_count()
    #TODO: If we get too many paths back and run into memory issues we could change so that only paths with score over 0 are stored in ELS module
    if param.multiprocess and num_cores > 1:
        import workerprocess
        import heapq
        print 'Entering ELS.BetweenScaffolds parallellized with ', num_cores, ' cores.'
        start = time.time()
        # load up work queue
        work_queue = multiprocessing.Queue()
        end = set()
        for node in G:
            end.add(node)
        nodes = G.nodes()
        nr_jobs = len(nodes)
        chunk = nr_jobs / (num_cores)
        counter = 0
        nr_processes = 0
        # partition equally many nodes in G to each core
        while counter < nr_jobs:
            work_queue.put((set(nodes[counter:counter + chunk]), G_prime, end))
            nr_processes += 1
            print 'node nr', counter, 'to', counter + chunk - 1, 'added'
            #print work_queue.get()
            counter += chunk

        # create a queue to pass to workers to store the results
        result_queue = multiprocessing.Queue()

        # spawn workers
        while not work_queue.empty():
            worker = workerprocess.Worker(work_queue.get(), result_queue)
            worker.start()

        # collect the results off the queue
        results = []
        for i in range(nr_processes):
            res = result_queue.get()
            results.append(res)

        def wrapper(func, args):
            return(func(*args))
        all_paths_sorted_wrt_score_itr = wrapper(heapq.merge, results) #tot_result
        all_paths_sorted_wrt_score = [i for i in all_paths_sorted_wrt_score_itr]
        elapsed = time.time() - start
        print "Elapsed time multiprocessing: ", elapsed

    else:
        start = time.time()
        end = set()
        for node in G:
            end.add(node)
        iter_nodes = end.copy()
        print 'Entering ELS.BetweenScaffolds single core'
        all_paths_sorted_wrt_score = ELS.BetweenScaffolds(G_prime, end, iter_nodes)
        elapsed = time.time() - start
        print "Elapsed time single core pathfinder: ", elapsed

    ################################################################

    start_end_node_update_storage = {}
    #print 'Total number of paths between scaffolds detected:', len(all_paths_sorted_wrt_score)
    for sublist in reversed(all_paths_sorted_wrt_score):
        path = sublist[2]
        bad_links = sublist[1]
        score = sublist[0]
        path_len = sublist[3]
        #print 'nr bad links path:',bad_links, 'Score:',score,'path length:', path_len #'Path:',path, 

        ## Need something here that keeps track on which contigs that are added to Scaffolds so that a
        ## contig is only present once in each path

        #print start_end_node_update_storage
        # Either a small contig/scaffold has been included in a path earlier and thus has moved it's object to Scaffolds (and changed index) 
        small_scaf_is_already_in = 0
        for scaf_ in path[1:-1]:
            if scaf_[0] not in small_scaffolds:
                small_scaf_is_already_in = 1
                #print 'At least one of the contigs is already in another scaffold'
                break
        if small_scaf_is_already_in:
            continue

        # A very special corner case (circular paths)
        if path[0][0] not in Scaffolds and path[-1][0] not in Scaffolds:
            try:
                strt = start_end_node_update_storage[path[0]][0]
                nd = start_end_node_update_storage[path[-1]][0]
                if strt[0] == nd[0]:
                    print 'Rare case (circular paths) detected and treated. '
                    continue
            except KeyError:
                pass

        # Or a large scaffold/contig has changed scaffold index due to one of it's sides is present in another path (we still want to allow for paths from the other side)
        case1 = 0
        case2 = 0
        if path[0][0] not in Scaffolds:
            if path[0] in start_end_node_update_storage:
                case1 = 1
            else:
                print 'Beginning is already in path'
                continue

        if path[-1][0] not in Scaffolds:
            if path[-1] in start_end_node_update_storage:
                case2 = 1
            else:
                print 'End is already in path'
                continue


        original_start_node = path[0]

        if path[0][0] not in Scaffolds:
            #large scaffold has changed index before. This suggested path is however from it's other side
            node_to_remove1 = path[0]
            path[0] = start_end_node_update_storage[node_to_remove1][0]
            #update the node on the other end of the end scaffold to point at the newest index
            node_to_refresh1 = start_end_node_update_storage[node_to_remove1][1]
            #print 'Enter 1'
            try:
                node_ptr = start_end_node_update_storage[ path[-1] ][1]
                #print '1.1', node_ptr,start_end_node_update_storage[ path[-1] ]
            except KeyError:
                other_side = 'L' if path[-1][1] == 'R' else 'R'
                node_ptr = (path[-1][0], other_side)
                #print '1.2', node_ptr, path[-1]
            start_end_node_update_storage[node_to_refresh1] = [(param.scaffold_indexer + 1, 'L'), node_ptr  ]
            #path pointer can be accesed only once needs to be destroyed after
            del start_end_node_update_storage[node_to_remove1]


        if path[-1][0] not in Scaffolds:
            #large scaffold has changed index before. This suggested path is however from it's other side
            #print 'case2.2'
            node_to_remove2 = path[-1]
            path[-1] = start_end_node_update_storage[node_to_remove2][0]
            #update the node on the other end of the end scaffold to point at the newest index
            node_to_refresh2 = start_end_node_update_storage[node_to_remove2][1]
            #print 'Enter 2'
            try:
                node_ptr = start_end_node_update_storage[ original_start_node ][1]
                #print '2.1', node_ptr, start_end_node_update_storage[ original_start_node ]
            except KeyError:
                other_side = 'L' if original_start_node[1] == 'R' else 'R'
                node_ptr = (original_start_node[0], other_side)
                #print '2.2', node_ptr,original_start_node          
            start_end_node_update_storage[node_to_refresh2] = [(param.scaffold_indexer + 1, 'R'), node_ptr ]
            #path pointer can be accesed only once needs to be destroyed after
            del start_end_node_update_storage[node_to_remove2]


        # Here we update the contigs that lies in small_contigs to Contigs. We need to do this here because
        # we update the scaffold index below

        # move all contig and scaffold objects from "small" structure to large structure to fit with UpdateInfo structure

        small_scafs = map(lambda i: path[i], filter(lambda i: i % 2 == 1, range(len(path) - 1)))
        #print small_scafs
        for item in small_scafs:
            scaf_obj = small_scaffolds[item[0]]
            Scaffolds[item[0]] = scaf_obj
            cont_objects = scaf_obj.contigs
            for obj_ in cont_objects:
                ctg_name = obj_.name
                Contigs[ctg_name] = obj_
                del small_contigs[ctg_name]
            del small_scaffolds[item[0]]
        ## Here we do the "joining of two scaffolds with the new path if no contig/scaffold is present
        ## in another path, we need to update "Scaffolds" structure here along as we go in order for
        ## the above dublette checking function to work

        #make the path a small linear graph
        G_ = nx.Graph()
#        if path[0][1] == 'L':
#            path.insert(0,(path[0][0],'R')) 
#        else: 
#            path.insert(0,(path[0][0],'L'))
#        if path[len(path)-1][1] == 'L':
#            path.insert(len(path),(path[len(path)-1][0],'R'))  
#        else:
#            path.insert(len(path),(path[len(path)-1][0],'L'))

        path.insert(0, (path[0][0], 'R')) if path[0][1] == 'L' else path.insert(0, (path[0][0], 'L'))
        path.insert(len(path), (path[-1][0], 'R'))  if path[-1][1] == 'L' else path.insert(len(path), (path[-1][0], 'L'))


        start_end_node_update_storage[path[0]] = 0
        start_end_node_update_storage[path[-1]] = 0
        G_.add_edges_from(zip(path[::1], path[1::]))

        for edge in G_.edges():
            try:
                G_[edge[0]][edge[1]]['nr_links'] = G_prime[edge[0]][edge[1]]['nr_links']
            except KeyError:
                print path
                try:
                    Scaffolds[edge[0][0]]
                    print edge[0][0] , 'is in Scaffolds'
                except KeyError:
                    print edge[0][0] , 'is not in Scaffolds'
                try:
                    Scaffolds[edge[1][0]]
                    print edge[1][0] , 'is in Scaffolds'
                except KeyError:
                    print edge[1][0] , 'is not in Scaffolds'

                try:
                    small_scaffolds[edge[0][0]]
                    print edge[0][0] , 'is in small_scaffolds'
                except KeyError:
                    print edge[0][0] , 'is not in small_scaffolds'
                try:
                    small_scaffolds[edge[1][0]]
                    print edge[1][0] , 'is in small_scaffolds'
                except KeyError:
                    print edge[1][0] , 'is not in small_scaffolds'

                try:
                    G_prime[edge[0]]
                    print edge[0] , 'is in G_prime'
                    print G_prime[edge[0]]
                except KeyError:
                    print edge[0] , 'is not in G_prime'
                try:
                    G_prime[edge[1]]
                    print edge[1] , 'is in G_prime'
                    print G_prime[edge[1]]
                except KeyError:
                    print edge[1] , 'is not in G_prime'
                G_[edge[0]][edge[1]]['nr_links'] = G_prime[edge[0]][edge[1]]['nr_links']
                sys.exit()

            try:
                G_[edge[0]][edge[1]]['obs'] = G_prime[edge[0]][edge[1]]['obs']
            except KeyError:
                #may be the two different sides of a cotig (has no gap dist)
                pass

        start = path[0]
        end = path[-1]
        prev_node = ('', '')
        pos = 0
        scaffold_length = 0
        contig_list = []
        param.scaffold_indexer += 1
        (G, contig_list, scaffold_length) = UpdateInfo(G_, Contigs, small_contigs, Scaffolds, small_scaffolds, start, prev_node, pos, contig_list, scaffold_length, dValuesTable, param)
        S = Scaffold.scaffold(param.scaffold_indexer, contig_list, scaffold_length, defaultdict(constant_large), defaultdict(constant_large), defaultdict(constant_small), defaultdict(constant_small))  #Create the new scaffold object 


        Scaffolds[S.name] = S        #include in scaffold library
        #add the new scaffold object to G_prime

        G_prime.add_node((S.name, 'L'))  #start node
        G_prime.add_node((S.name, 'R'))  # end node
        G_prime.add_edge((S.name, 'L'), (S.name, 'R'), nr_links=None)
        for nbr in G_prime.neighbors(start):
            nr_links_ = G_prime[start][nbr]['nr_links']
            if nr_links_:
                obs_ = G_prime[start][nbr]['obs']
                G_prime.add_edge((S.name, 'L'), nbr, nr_links=nr_links_, obs=obs_)

        for nbr in G_prime.neighbors(end):
            nr_links_ = G_prime[end][nbr]['nr_links']
            if nr_links_:
                obs_ = G_prime[end][nbr]['obs']
                G_prime.add_edge((S.name, 'R'), nbr, nr_links=nr_links_, obs=obs_)

        #remove the old scaffold objects from G_prime
        G_prime.remove_nodes_from(path)

        #updated beginning
        if case1 and not case2:
            start_end_node_update_storage[node_to_refresh1] = [(S.name, 'L'), path[-1] ]
            start_end_node_update_storage[path[-1]] = [(S.name, 'R'), node_to_refresh1 ]
        elif case2 and not case1:
            start_end_node_update_storage[path[0]] = [(S.name, 'L'), node_to_refresh2 ]
            start_end_node_update_storage[node_to_refresh2] = [(S.name, 'R'), path[0] ]
        elif case1 and case2:
            start_end_node_update_storage[node_to_refresh1] = [(S.name, 'L'), node_to_refresh2 ]
            start_end_node_update_storage[node_to_refresh2] = [(S.name, 'R'), node_to_refresh1 ]
        else:
            start_end_node_update_storage[path[0]] = [(S.name, 'L'), path[-1] ]
            start_end_node_update_storage[path[-1]] = [(S.name, 'R'), path[0] ]

    return(start_scaf_index)

def GiveLinkConnection(Contigs, contig_objects1, contig_objects2, side1, side2, nr_links):
    if side1 == 'R' and side2 == 'L':
        max_pos = 0
        for contig in contig_objects1:
            if contig.position >= max_pos:
                linking_contig1 = contig
                max_pos = contig.position
        min_pos = sys.maxint
        for contig in contig_objects2:
            if contig.position <= min_pos:
                linking_contig2 = contig
                min_pos = contig.position

        linking_contig1.links[linking_contig2.name] = nr_links
        linking_contig2.links[linking_contig1.name] = nr_links


    elif side1 == 'L' and side2 == 'R':
        max_pos = 0
        for contig in contig_objects2:
            if contig.position >= max_pos:
                linking_contig2 = contig
                max_pos = contig.position

        min_pos = sys.maxint
        for contig in contig_objects1:
            if contig.position <= min_pos:
                linking_contig1 = contig
                min_pos = contig.position
        linking_contig1.links[linking_contig2.name] = nr_links
        linking_contig2.links[linking_contig1.name] = nr_links


    elif side1 == 'R' and side2 == 'R':
        max_pos = 0
        for contig in contig_objects1:
            if contig.position >= max_pos:
                linking_contig1 = contig
                max_pos = contig.position

        max_pos = 0
        for contig in contig_objects2:
            if contig.position >= max_pos:
                linking_contig2 = contig
                max_pos = contig.position
        linking_contig1.links[linking_contig2.name] = nr_links
        linking_contig2.links[linking_contig1.name] = nr_links


    elif side1 == 'L' and side2 == 'L':
        min_pos = sys.maxint
        for contig in contig_objects1:
            if contig.position <= min_pos:
                linking_contig1 = contig
                min_pos = contig.position
        min_pos = sys.maxint
        for contig in contig_objects2:
            if contig.position <= min_pos:
                linking_contig2 = contig
                min_pos = contig.position

        linking_contig1.links[linking_contig2.name] = nr_links
        linking_contig2.links[linking_contig1.name] = nr_links
