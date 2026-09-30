Resedential Proxy catalog i sshowing this:


#1	pk.decodo.com:10001	PK	ACTIVE	13	1	—	09:19:03	
#2	pk.decodo.com:10002	PK	ACTIVE	0	1	Imperva WAF / Timeout Block	Never	
#3	pk.decodo.com:10003	PK	ACTIVE	74	49	—	09:51:56	
#4	pk.decodo.com:10004	PK	ACTIVE	0	1	Imperva WAF / Timeout Block	Never	
#5	pk.decodo.com:10005	PK	ACTIVE	0	1	Imperva WAF / Timeout Block	Never	
#6	pk.decodo.com:10006	PK	ACTIVE	0	1	Imperva WAF / Timeout Block	Never	
#7	pk.decodo.com:10007	PK	ACTIVE	22	0	—	07:26:44	
#8	pk.decodo.com:10008	PK	ACTIVE	0	1	Imperva WAF / Timeout Block	Never	
#9	pk.decodo.com:10009	PK	ACTIVE	27	0	—	09:09:05	
#10	pk.decodo.com:10010	PK	ACTIVE	22	1	Imperva WAF / Timeout Block	14:49:24	


I see there are 49 fails for: #3	pk.decodo.com:10003	

are we not putting failed proxies to cool down/quarantine? because i see all are still active; although #3 had 49 failures!


this also points me to another thought that with so much checks over short periods of time; the accounts might start getting banned/rate limited, no?

what is the strategy here? We dont want our staff accounts getting banned/rate limited
