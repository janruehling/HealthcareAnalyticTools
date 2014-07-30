"""
Script to extract a sub-graph from the Teaming dataset data set that is loaded in a MySQL
database. This script can use any variable in the node database as a selector. The node
database that you are connecting is "loading_teaming_nodes.sql". The "loading_teaming_nodes.sql"
populates the nodes database from provider for specific states.

A database connection is made through a MySQL database that has a DSN called teaming.

"""

__author__ = 'Janos G. Hajagos'

import pyodbc as odbc
import networkx as nx
import pprint
import sys
import csv
import json
import os

def load_configuration(file_name="config.json.old.example"):
    with open(file_name, "r") as f:
        configuration = json.load(f)
        return configuration


"""
Save a copy of config.json.old.example to config.json.old. Here you can configure how the script runs and which
tables are linked to.
"""

if os.path.exists("config.json.old"): # Checks for a configuration file
    config = load_configuration("config.json.old")
else: # if no configuration file exists it loads the default "config.json.old.example"
    config = load_configuration()

#Set the configuration for the script
REFERRAL_TABLE_NAME = config["REFERRAL_TABLE_NAME"]
NPI_DETAIL_TABLE_NAME = config["NPI_DETAIL_TABLE_NAME"]
FIELD_NAME_FROM_RELATIONSHIP = config["FIELD_NAME_FROM_RELATIONSHIP"]
FIELD_NAME_TO_RELATIONSHIP = config["FIELD_NAME_TO_RELATIONSHIP"]
FIELD_NAME_WEIGHT = config["FIELD_NAME_WEIGHT"]


def logger(string_to_write=""):
    """Print to the standard input"""
    print(string_to_write)

def get_new_cursor(dsn_name="teaming"):
    """Open the connection to the database and returns a cursor for executing queries"""
    logger("Opening connection %s" % dsn_name)
    connection = odbc.connect("DSN=%s" % dsn_name, autocommit=True)
    return connection.cursor()


def row_to_dictionary(row_obj, exclude_None = True):
    """Convert a row to a Python dictionary that is easier to work with"""
    column_names = [desc[0] for desc in row_obj.cursor_description]
    row_dict = {}
    for i in range(len(column_names)):
        if exclude_None:
            if row_obj[i] is not None:
                row_dict[column_names[i]] = row_obj[i]
    return row_dict


def add_nodes_to_graph(cursor, graph, node_type, label_name = None):
    """Add nodes to the graph from the return query"""
    i = 0
    nodes_initial = len(graph.nodes())
    for node in cursor:
        attributes = row_to_dictionary(node)
        if label_name:
            if label_name in attributes:
                attributes["Label"] = attributes[label_name]
        attributes["node_type"] = node_type
        graph.add_node(node.npi, attributes)
        i += 1
    logger("Read %s rows from table" % i)
    nodes_final = len(graph.nodes())
    logger("Imported %s nodes" % (nodes_final - nodes_initial,))
    return graph


def add_edges_to_graph(cursor, graph, name="shares patients"):
    """Add edges to the graph from the query"""
    i = 0
    counter_dict = {}

    for edge in cursor:
        if edge.to_node_type == 'C' and edge.from_node_type == 'C':
            edge_type = 'core-to-core'
        elif edge.to_node_type == 'L' and edge.from_node_type == 'C':
            edge_type = 'leaf-to-core'
        elif edge.to_node_type == 'C' and edge.from_node_type == 'L':
            edge_type = 'core-to-leaf'
        elif edge.to_node_type == 'L' and edge.from_node_type == 'L':
            edge_type = 'leaf-to-leaf'
        else:
            edge_type = "None"

        if edge_type in counter_dict:
            counter_dict[edge_type] += 1
        else:
            counter_dict[edge_type] = 0

        graph.add_edge(edge[0], edge[1], weight=edge[2], edge_type=edge_type)
        i += 1

    logger("Imported %s edges" % i)
    logger("Edge types imported")
    logger(pprint.pformat(counter_dict))
    return graph


def extract_provider_network(where_criteria, referral_table_name=REFERRAL_TABLE_NAME, npi_detail_table_name=NPI_DETAIL_TABLE_NAME,
         field_name_to_relationship=FIELD_NAME_TO_RELATIONSHIP, field_name_from_relationship=FIELD_NAME_FROM_RELATIONSHIP,
         file_name_prefix="",add_leaf_to_leaf_edges=False, node_label_name="provider_name",
         field_name_weight=FIELD_NAME_WEIGHT, add_leaf_nodes=True, graph_type="directed", csv_output=True, directory="./"):
    """Main script for extracting the provider graph from the MySQL database."""

    cursor = get_new_cursor() #Get an active connection to the database

    #Show the configuration that the script is running with
    #To pipe the output to a file use the > operator to redirect output to a file:
    #python extract_providers_to_graphml.py "zip5 in ('02535,'02539','02568','02557','02575')" mi_prov Leaf-edges > log.txt

    logger("Configuration")
    logger("Selection criteria for subset graph: %s" % where_criteria)
    logger("Referral table _name: %s" % referral_table_name)
    logger("NPI detail table name: %s" % npi_detail_table_name)
    logger("Nodes will be labelled by: %s" % node_label_name)
    logger("Leaf-to-leaf edges will be exported? %s" % add_leaf_to_leaf_edges)

    logger()
    drop_table_sql = "drop table if exists npi_to_export_to_graph;" # This table is a temporary table but it is not designed for concurrent running of the script
    logger(drop_table_sql)
    cursor.execute(drop_table_sql) # Drop table that was used in the last export of a provider graph

    logger()
    create_table_sql = "create table npi_to_export_to_graph (npi char(10),node_type char(1));" # Create the temporary table for storing NPI extracted from the graph
    # Type of a node can be either core or leaf. The core node is one that is directly selected based on the selection criteria. The selection
    # criteria is specified as a SQL where clause on the commandline. The leaf nodes are nodes that are connected through a shared edge with the core.

    logger(create_table_sql)
    cursor.execute(create_table_sql)

    # Get NPI from each side of the relationship
    # The teaming database is a directed graph. Between two nodes (providers) they can have different connection directions.

    query_first_part = """select distinct npi from %s rt1 join %s tnd1 on rt1.%s = tnd1.npi where %s""" % (referral_table_name,npi_detail_table_name,field_name_from_relationship, where_criteria)
    query_second_part = """select distinct npi from %s rt2 join %s tnd2 on rt2.%s = tnd2.npi where %s""" % (referral_table_name,npi_detail_table_name,field_name_to_relationship, where_criteria)
    core_node_query_to_execute = "insert into npi_to_export_to_graph (npi,node_type) select t.*,'C' from (\n%s\nunion\n%s)\nt;" % (query_first_part, query_second_part)

    logger(core_node_query_to_execute)
    cursor.execute(core_node_query_to_execute)

    # Add an index to the temporary table to make extraction of node detail to happen in a reasonable amount of time
    logger("Adding indices")
    cursor.execute("create unique index idx_primary_npi_graph on npi_to_export_to_graph(npi);")
    npi_detail_query_to_execute = "select * from npi_to_export_to_graph neg join %s tnd on tnd.npi = neg.npi" % npi_detail_table_name
    logger(npi_detail_query_to_execute)

    # Populate the nodes are directly selected criteria
    logger("Populating core nodes")
    cursor.execute(npi_detail_query_to_execute)

    # Select the default directed graph. Here we call the networkx Graph object
    if graph_type == "directed":
        ProviderGraph = nx.DiGraph()
    elif graph_type == "undirected": # Warning this is not tested currently
        ProviderGraph = nx.Graph()

    ProviderGraph = add_nodes_to_graph(cursor, ProviderGraph, "core", label_name=node_label_name)

    # If leaf nodes are select the script will import them into the database
    if add_leaf_nodes:
        logger("Adding leaf nodes")

        add_leaf_node_query_to_execute = """insert into npi_to_export_to_graph (npi,node_type)
        select t.npi,'L'  from (
      select distinct rt1.%s as npi FROM npi_to_export_to_graph neg1 join %s rt1 on rt1.%s = neg1.npi
        union
      select distinct rt2.%s as npi FROM npi_to_export_to_graph neg2 join %s rt2 on rt2.%s = neg2.npi
      ) t where npi not in (select npi from npi_to_export_to_graph)""" % (field_name_from_relationship, referral_table_name, field_name_to_relationship, field_name_to_relationship, referral_table_name, field_name_from_relationship)

        logger(add_leaf_node_query_to_execute)
        cursor.execute(add_leaf_node_query_to_execute)

        # These are the connected nodes to the primary nodes
        logger("Populating leaf nodes")

        # Populate the details to the leaf nodes
        populate_leaf_nodes_query_to_execute = """select * from npi_to_export_to_graph neg join %s tnd
            on tnd.npi = neg.npi where neg.node_type = 'L'""" % npi_detail_table_name
        logger(populate_leaf_nodes_query_to_execute)
        cursor.execute(populate_leaf_nodes_query_to_execute)
        ProviderGraph = add_nodes_to_graph(cursor, ProviderGraph, "leaf", label_name=node_label_name)

    # Add in the edges to the data
    logger("Populating edges")

    query_first_part_edges = """select rt1.%s,rt1.%s, rt1.%s,
  neg1.node_type as to_node_type, negf.node_type as from_node_type
   from %s rt1 join npi_to_export_to_graph neg1 on rt1.%s = neg1.npi
       join npi_to_export_to_graph negf on negf.npi = rt1.%s
  where neg1.node_type = 'C'""" % (field_name_to_relationship, field_name_from_relationship, field_name_weight, referral_table_name, field_name_to_relationship, field_name_from_relationship)

    query_second_part_edges = """select rt2.%s, rt2.%s, rt2.%s,
  negt.node_type as to_node_type, neg2.node_type as from_node_type
   from %s rt2 join npi_to_export_to_graph neg2 on rt2.%s = neg2.npi
       join npi_to_export_to_graph negt on negt.npi = rt2.%s
  where neg2.node_type = 'C'""" % (field_name_to_relationship, field_name_from_relationship, field_name_weight, referral_table_name, field_name_from_relationship, field_name_to_relationship)

    add_core_query_to_execute = "%s\nunion\n%s" % (query_first_part_edges, query_second_part_edges)

    # Add the leaf edges to the data
    logger(add_core_query_to_execute)
    cursor.execute(add_core_query_to_execute)
    ProviderGraph = add_edges_to_graph(cursor, ProviderGraph)

    if add_leaf_to_leaf_edges: #Danger is that there are too many leaves
        logger("Add leaf edges")

        leaf_query_to_execute = """select rt3.%s, rt3.%s, rt3.%s,
        negt3.node_type as to_node_type, negf3.node_type as from_node_type
      from %s rt3 join npi_to_export_to_graph negt3 on rt3.%s = negt3.npi
      join npi_to_export_to_graph negf3 on rt3.%s = negf3.npi
      where negt3.node_type = 'L' and negf3.node_type = 'L'
      ;""" % (field_name_to_relationship, field_name_from_relationship, field_name_weight, referral_table_name,
              field_name_to_relationship, field_name_from_relationship)
        cursor.execute(leaf_query_to_execute)
        logger(leaf_query_to_execute)
        add_edges_to_graph(cursor, ProviderGraph)
    else:
        logger("Leaf-to-leaf edges were not selected for export")

    print(nx.info(ProviderGraph))


    logger("Writing GraphML file")
    nx.write_graphml(ProviderGraph, os.path.join(directory, file_name_prefix + "_provider_graph.graphml"))

    if csv_output:
        csv_edge_file_name = os.path.join(directory, file_name_prefix + "_edge_list_with_weights.csv")

        logger("Writing CSV of edges with weights")
        with open(csv_edge_file_name,"wb") as f:
            csv_edges = csv.writer(f)
            for node1 in ProviderGraph.edge:
                for node2 in ProviderGraph.edge[node1]:
                    npi_from = node1
                    npi_to = node2
                    edge_data = ProviderGraph[node1][node2]
                    csv_edges.writerow((npi_from, npi_to, edge_data["weight"]))

        csv_node_file_name = os.path.join(directory, file_name_prefix + "_node_db.csv")
        logger("Writing CSV of nodes with attributes")

        with open(csv_node_file_name, "wb") as fw:
            i = 0
            csv_nodes = csv.writer(fw)
            for node in ProviderGraph.node:

                node_dict = ProviderGraph.node[node]
                if i == 0:
                    header = node_dict.keys()
                    header.sort()
                    header = ["node_id"] + header

                    csv_nodes.writerow(header)

                row_to_write = [node]
                for attribute in header:
                    if attribute in node_dict:
                        value_to_write = node_dict[attribute]
                    else:
                        value_to_write = ''

                    row_to_write += [value_to_write]
                csv_nodes.writerow(row_to_write)

                i += 1


if __name__ == "__main__":
    number_of_args = len(sys.argv)
    if number_of_args == 1:
        print("""Usage:
python extract_providers_to_graphml.py "condition='1234567890'" file_name_prefix [no-leaf-nodes] [Leaf-edges]""")

    elif len(sys.argv) == 3:
        extract_provider_network(sys.argv[1], file_name_prefix=sys.argv[2])
    else:
        if "no-leaf-nodes" in sys.argv[3:]:
            leaf_nodes = False
        else:
            leaf_nodes = True

        if "leaf-edges" in sys.argv[3:]:
            leaf_edges = True
        else:
            leaf_edges = False

        extract_provider_network(sys.argv[1], file_name_prefix=sys.argv[2], add_leaf_nodes=leaf_nodes,
                                 add_leaf_to_leaf_edges=leaf_edges)