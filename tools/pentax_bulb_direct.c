/* Drive the K-1 II timed-Bulb action through libgphoto2 with an exact hold.
 * gphoto2 --wait-event adds ~1s per hold, so it cannot measure 1s fidelity.
 * Abilities come from gp_abilities_list_lookup_model(), not a hand-filled struct. */
#include <gphoto2/gphoto2-camera.h>
#include <gphoto2/gphoto2-abilities-list.h>
#include <gphoto2/gphoto2-port-info-list.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <gphoto2/gphoto2-port-log.h>

static int applied_s=-1;
static void logcb(GPLogLevel level,const char*domain,const char*str,void*data){
	if(!str)return;
	if(strstr(str,"camera-timed fallback applying")){
		int n=sscanf(strstr(str,"applying "),"applying %d",&n)==1?n:-1;
		applied_s=n;
		printf("    DRIVER: %s\n",str);
	}
}

static double now_s(void){struct timespec ts;clock_gettime(CLOCK_MONOTONIC,&ts);return ts.tv_sec+ts.tv_nsec/1e9;}

static double t_gc,t_sv,t_sc;
static CameraWidget *cached_root=NULL;
static int set_bulb(Camera *cam,int val){
	if(cached_root){
		CameraWidget *ch=NULL; double a=now_s();
		int rc=gp_widget_get_child_by_name(cached_root,"bulb",&ch);
		if(rc){printf("    child bulb -> %d\n",rc);return rc;}
		rc=gp_widget_set_value(ch,&val);
		double b=now_s();
		if(!rc) rc=gp_camera_set_config(cam,cached_root,NULL);
		t_gc=0; t_sv=b-a; t_sc=now_s()-b;
		return rc;
	}
	CameraWidget *root=NULL,*child=NULL;
	double a=now_s();
	int rc=gp_camera_get_config(cam,&root,NULL);
	t_gc=now_s()-a;
	if(rc){printf("    get_config -> %d\n",rc);return rc;}
	rc=gp_widget_get_child_by_name(root,"bulb",&child);
	if(rc){printf("    child bulb -> %d\n",rc);gp_widget_free(root);return rc;}
	{double b=now_s(); rc=gp_widget_set_value(child,&val); t_sv=now_s()-b;}
	{double c=now_s(); if(!rc) rc=gp_camera_set_config(cam,root,NULL); t_sc=now_s()-c;}
	gp_widget_free(root);
	if(rc) printf("    set bulb=%d -> %d\n",val,rc);
	return rc;
}
static void show(Camera *cam,const char *name){
	CameraWidget *root=NULL,*child=NULL;const char *val=NULL;
	if(gp_camera_get_config(cam,&root,NULL))return;
	if(gp_widget_get_child_by_name(root,name,&child)==GP_OK&&child&&
	   gp_widget_get_value(child,&val)==GP_OK&&val)
		printf("    %-18s %s\n",name,val);
	gp_widget_free(root);
}
int main(int argc,char**argv){
	const char *port=argc>1?argv[1]:"usb:001,011";
	const char *model=argc>2?argv[2]:"Pentax K-1 Mark II (PTP mode)";
	double hold=argc>3?atof(argv[3]):1.0;
	int iters=argc>4?atoi(argv[4]):3;
	int cached=argc>5?atoi(argv[5]):0;
	CameraAbilitiesList *alist=NULL;CameraAbilities ab;
	int idx,i,worst=0,edges_ok=1,dur_ok=1;
	gp_log_add_func(GP_LOG_DEBUG,logcb,NULL);
	if(gp_abilities_list_new(&alist)||gp_abilities_list_load(alist,NULL)){printf("load failed\n");return 1;}
	idx=gp_abilities_list_lookup_model(alist,model);
	if(idx<0){printf("model not found: %s (idx=%d)\n",model,idx);return 1;}
	if(gp_abilities_list_get_abilities(alist,idx,&ab)){printf("get_abilities failed\n");return 1;}
	printf("model=%s id=%s hold=%.3fs iters=%d cached=%d\n",model,ab.id,hold,iters,cached);
	for(i=1;i<=iters;i++){
		Camera *cam=NULL;double t0,held,sleep_end,stop_cost;int s,e,dev;
		if(gp_camera_new(&cam)){printf("camera_new failed\n");return 1;}
		if(gp_camera_set_abilities(cam,ab)){printf("set_abilities failed\n");return 1;}
		{
			GPPortInfoList *pl=NULL;GPPortInfo info;int pi;
			if(gp_port_info_list_new(&pl)||gp_port_info_list_load(pl)){printf("port list load failed (IOLIBS wrong?)\n");return 1;}
			pi=gp_port_info_list_lookup_path(pl,port);
			if(pi<0){printf("port path not found: %s\n",port);return 1;}
			gp_port_info_list_get_info(pl,pi,&info);
			if(gp_camera_set_port_info(cam,info)){printf("set_port_info failed\n");return 1;}
			gp_port_info_list_free(pl);
		}
		if(gp_camera_init(cam,NULL)){printf("camera_init failed\n");return 1;}
		applied_s=-1;
		if(cached){ if(gp_camera_get_config(cam,&cached_root,NULL)){printf("cache get_config failed\n");return 1;} }
		s=set_bulb(cam,1); t0=now_s();
		printf("    start edge: get_config=%.3fs set_value=%.3fs set_config=%.3fs\n",t_gc,t_sv,t_sc);
		{struct timespec ts={(time_t)hold,(long)((hold-(double)(time_t)hold)*1e9)};nanosleep(&ts,NULL);}
		sleep_end=now_s(); e=set_bulb(cam,0); stop_cost=now_s()-sleep_end;
		printf("    stop  edge: get_config=%.3fs set_value=%.3fs set_config=%.3fs\n",t_gc,t_sv,t_sc);
		held=sleep_end-t0;
		printf("iter %d: sleep=%.3fs stop_edge=%.3fs  driver applied=%d  start=%d stop=%d\n",
		       i,held,stop_cost,applied_s,s,e);
		show(cam,"shutterspeed"); show(cam,"pentaxconditions");
		if(s||e) edges_ok=0;
		dev=(int)((held-hold)*1000.0); if(dev<0)dev=-dev;
		if(dev>worst) worst=dev;
		if(applied_s!=(int)(hold+0.5)) dur_ok=0;
		if(cached_root){gp_widget_free(cached_root);cached_root=NULL;}
		gp_camera_exit(cam,NULL); gp_camera_free(cam);
	}
	printf("\n--- summary ---\nmax hold deviation: %d ms\nboth edges accepted : %s\nduration correct    : %s\n",
		worst,edges_ok?"yes":"NO",dur_ok?"yes":"NO");
	gp_abilities_list_free(alist);
	return (edges_ok&&dur_ok)?0:1;
}
