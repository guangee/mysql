# The proper term is pseudo_replica_mode, but we use this compatibility alias
# to make the statement usable on server versions 8.0.24 and older.
/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=1*/;
/*!50003 SET @OLD_COMPLETION_TYPE=@@COMPLETION_TYPE,COMPLETION_TYPE=0*/;
DELIMITER /*!*/;
# at 4
#251128 14:57:24 server id 1  end_log_pos 126 CRC32 0xe9d830c9 	Start: binlog v 4, server v 8.0.35 created 251128 14:57:24 at startup
ROLLBACK/*!*/;
BINLOG '
VLgpaQ8BAAAAegAAAH4AAAAAAAQAOC4wLjM1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
AAAAAAAAAAAAAAAAAABUuClpEwANAAgAAAAABAAEAAAAYgAEGggAAAAICAgCAAAACgoKKioAEjQA
CigAAckw2Ok=
'/*!*/;
/*!50616 SET @@SESSION.GTID_NEXT='AUTOMATIC'*//*!*/;
# at 126
# at 157
#251128 14:57:26 server id 1  end_log_pos 204 CRC32 0xef38660f 	Rotate to mysql-bin.000002  pos: 4
# at 4
#251128 14:57:26 server id 1  end_log_pos 126 CRC32 0x84e0ae93 	Start: binlog v 4, server v 8.0.35 created 251128 14:57:26
BINLOG '
